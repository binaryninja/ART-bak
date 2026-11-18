"""Bounded extraction of instance candidates from operator-supplied evidence."""
import hashlib
import json
import re
from .definitions import instance_origin

def discover(text):
    if not isinstance(text,str) or len(text.encode())>262144:raise ValueError('Discovery text exceeds limit')
    try:
        data=json.loads(text)
        if isinstance(data,dict) and 'url' in data:
            return [dict(url=instance_origin(data['url']),username=data.get('username',''),password=data.get('password',''))]
    except (ValueError,TypeError):pass
    starts=list(re.finditer(r'(?im)^\s*(?:url|instance(?: url)?)\s*[:=]\s*(?=https://)',text))
    if len(starts)>1:
        if re.search(r'(?im)^\s*(?:user(?:name)?|password|pass)\s*[:=]',text[:starts[0].start()]):raise ValueError('Unassociated credentials')
        result=[]
        for index,start in enumerate(starts):
            block=text[start.start():starts[index+1].start() if index+1<len(starts) else len(text)]
            items=discover(block)
            if len(items)!=1:raise ValueError('Ambiguous instance block')
            result.extend(items)
        if len(result)>20:raise ValueError('Too many discovery candidates')
        return result
    urls=list(dict.fromkeys(instance_origin(m.rstrip('.,;)')) for m in re.findall(r'https://[A-Za-z0-9.-]+(?::443)?(?:/[^\s<>"\']*)?',text) if '.service-now.com' in m.lower()))
    if len(urls)>20:raise ValueError('Too many discovery candidates')
    user=re.findall(r'(?im)^\s*(?:user(?:name)?)\s*[:=]\s*(\S+)\s*$',text)
    password=re.findall(r'(?im)^\s*(?:password|pass)\s*[:=]\s*(\S+)\s*$',text)
    if (user or password) and (len(urls)!=1 or len(user)!=1 or len(password)!=1):raise ValueError('Ambiguous credential association')
    return [dict(url=url,username=user[0] if user else '',password=password[0] if password else '') for url in urls]

def source_metadata(text,label='uploaded text'):
    return {'kind':'text-discovery','sha256':hashlib.sha256(text.encode()).hexdigest(),
            'label':re.sub(r'[^A-Za-z0-9_. /-]','_',label)[:160]}


class DiscoveryDispatcher:
    """One admission path for uploads, artifact reads and discovered assets."""
    def __init__(self,runtime):self.runtime=runtime

    def scan_assets(self):
        """Consume committed in-scope asset changes, with a durable cursor."""
        from ..db.core import Query
        runtime=self.runtime
        hosts=sorted(origin.removeprefix('https://') for origin in runtime.store.allowed_origins)
        key='servicenow-assets:'+hashlib.sha256(json.dumps(hosts).encode()).hexdigest()
        rows=runtime.pool.fetch_all(Query("""SELECT e.id,a.id,COALESCE(a.url,''),COALESCE(a.domain,''),a.technologies,
CASE WHEN octet_length(array_to_json(a.auth)::text)<=262144 THEN array_to_json(a.auth)::text ELSE '[]' END
FROM artex_asset_discovery_events e JOIN assets a ON a.id=e.asset_id
LEFT JOIN artex_asset_discovery_receipts r ON r.event_id=e.id AND r.source_key=$1
WHERE r.event_id IS NULL AND a.type IN ('service','endpoint','subdomain')
AND (lower(a.domain)=ANY($2::text[]) OR lower(split_part(split_part(a.url,'://',2),'/',1))=ANY($3::text[]))
ORDER BY e.id LIMIT 100""",(key,hosts,hosts+[h+':443' for h in hosts])))
        for event_id,asset_id,url,domain,technologies,auth in rows:
            try:origin=runtime.store.approved(url or 'https://'+domain)
            except (PermissionError,ValueError):continue
            if not origin.endswith('.service-now.com') and not any('servicenow' in str(t).replace(' ','').lower() for t in technologies):continue
            source={'kind':'asset-discovery','asset_id':str(asset_id)}
            runtime.submit('servicenow.inventory',origin,'',source)
            entries=json.loads(auth) if isinstance(auth,str) else auth
            for entry in (entries or [])[:20]:
                if not isinstance(entry,dict):continue
                user,password=entry.get('username'),entry.get('password')
                if not isinstance(user,str) or not isinstance(password,str) or not user or not password:continue
                from ..recon.servicenow import ServiceNowInventory
                try:ServiceNowInventory(user,password,origin=origin,authorized_origins=runtime.store.allowed_origins)
                except ValueError:continue
                ref=runtime.credentials.put(origin,user,password)
                runtime.submit('servicenow.inventory',origin,ref,source)
        for row in rows:
            runtime.pool.execute(Query('INSERT INTO artex_asset_discovery_receipts(source_key,event_id) VALUES($1,$2) ON CONFLICT DO NOTHING',(key,row[0])))
        return len(rows)

    def ingest(self,text,label='uploaded text',source_task_id=None):
        runtime=self.runtime;source=source_metadata(text,label);results=[]
        if source_task_id is not None:source['task_id']=str(source_task_id)
        candidates=discover(text)
        for candidate in candidates:
            try:origin=runtime.store.approved(candidate['url'])
            except PermissionError:
                results.append({'origin':candidate['url'],'state':'out_of_scope'});continue
            user,password=candidate['username'],candidate['password']
            if user or password:
                from ..recon.servicenow import ServiceNowInventory
                ServiceNowInventory(user,password,origin=origin,authorized_origins=runtime.store.allowed_origins)
                ref=runtime.credentials.put(origin,user,password)
            else:ref=''
            record=runtime.submit('servicenow.inventory',origin,ref,source)
            results.append({'origin':origin,'task_id':str(record['task_id']),'assessment_id':record['id']})
        return results

    def read_observer(self,*,authorize):
        """Bind to ScopedFilesystem(read_observer=...) for an owned worker.

        The host authorizer must check the current task/generation and artifact
        scope. Discovery does not infer that authority from a pathname.
        """
        if not callable(authorize):raise ValueError('Artifact discovery authority required')
        def observe(context,path,text):
            context.check()
            if not path.lower().endswith(('.txt','.md','.json')):return text
            if authorize(context,path) is not True:raise PermissionError('Artifact discovery denied')
            candidates=discover(text)
            self.ingest(text,path,context.get('task_id'))
            for candidate in candidates:
                if candidate['password']:text=text.replace(candidate['password'],'[credential stored privately]')
            context.check()
            if authorize(context,path) is not True:raise PermissionError('Artifact discovery ownership changed')
            return text
        return observe
