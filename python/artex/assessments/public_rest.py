"""Source-pinned anonymous REST assessment. No discovered URL grants authority."""
import hashlib
import json
from pathlib import Path
import re
import uuid
from http.cookies import SimpleCookie
from urllib.parse import quote, urlencode, urlsplit
from ..db.core import Query
from ..tools.http import ScopedHTTP

REVIEWED_BYTES=Path(__file__).with_name('servicenow_reviewed_probes.json').read_bytes()
REVIEWED=json.loads(REVIEWED_BYTES)
POLICY_DIGEST=hashlib.sha256(REVIEWED_BYTES).hexdigest()
READ_PATHS=('/api/now/table/sys_security_acl','/api/now/table/sys_security_acl_role',
    '/api/now/table/sys_api_access_policy','/api/now/table/sys_ws_operation')

def parent(runtime,record):
    rows=runtime.pool.fetch_all(Query("SELECT id,task_id,report->>'run_id' FROM artex_assessments WHERE definition='servicenow.inventory' AND origin=$1 AND credential_ref=$2 AND report IS NOT NULL AND state='done' ORDER BY created_at DESC LIMIT 1",(record['origin'],record['credential_ref'])))
    return dict(zip(('id','task_id','run_id'),rows[0])) if rows else None

def inherited(runtime,record,resource):
    source=parent(runtime,record)
    if not source:return None
    value=runtime.store.observations(source['id']).get(resource)
    return dict(value,source_assessment=source) if value is not None else None

def read_rows(inventory,context,table,fields,query):
    records=[];pages=[]
    for offset in range(0,10000,250):
        path='/api/now/table/'+table+'?'+urlencode(dict(sysparm_query=query+'^ORDERBYsys_id',sysparm_fields=fields,sysparm_limit=250,sysparm_offset=offset,sysparm_exclude_reference_link='true'))
        response=json.loads(inventory.read_registered(context,path));pages.append({k:response.get(k) for k in ('status','body_sha256','truncated')})
        if response.get('status')!=200 or response.get('truncated'):return dict(records=records,pages=pages,complete=False,limitation='metadata unavailable or bounded')
        value=json.loads(response['body']).get('result')
        if not isinstance(value,list):raise ValueError('Unexpected metadata response')
        records.extend(value)
        if len(value)<250:return dict(records=records,pages=pages,complete=True)
    return dict(records=records,pages=pages,complete=False,limitation='metadata record limit')

def authorization(inventory,context):
    return dict(resource='public_rest_authorization',status=200,
        rest_acls=read_rows(inventory,context,'sys_security_acl','sys_id,name,operation,active,condition,script,admin_overrides','active=true^type=rest_endpoint'),
        roles=read_rows(inventory,context,'sys_security_acl_role','sys_security_acl,sys_security_acl.name,sys_user_role.name','sys_security_acl.type=rest_endpoint'),
        access_policies=read_rows(inventory,context,'sys_api_access_policy','sys_id,name,active,api,api_resource,api_path,http_method,apply_global,apply_all,apply_all_resources,apply_all_methods,apply_all_versions,version,enforce_for_interactive_session','active=true'))

def candidates(observations):
    scripts={r['sys_id']:r for r in observations.get('api_scripts',{}).get('records',[])}
    definitions={r['sys_id']:r for r in observations.get('api_definitions',{}).get('records',[])}
    result=[]
    for resource in observations.get('api_resources',{}).get('records',[]):
        if str(resource.get('active')).lower()!='true' or str(resource.get('requires_authentication')).lower()!='false':continue
        row=dict(scripts.get(resource['sys_id'],{}),**resource)
        row['definition']=definitions.get(row.get('web_service_definition'),{})
        source=row.get('operation_script','')
        surfaces=[]
        for pattern,label in ((r'GlideRecord|GlideAggregate|\.getRecord\(','record and field authorization'),(r'JWT|[Tt]oken|[Ss]ignature|CSR','token, signature or certificate validation'),(r'orderNow|submitProducer|\.insert\(|\.update\(|\.delete|triggerMosaic','state-changing operation'),(r'gs\.(info|warn|error)|\.log\(','log disclosure'),(r'permalink|attachment|document','document or attachment access'),(r'queryParams|pathParams|request\.body','attacker-controlled input')):
            if re.search(pattern,source+' '+row.get('operation_uri','')):surfaces.append(label)
        row['attack_surface']=surfaces or ['effective REST authorization and response disclosure']
        row['source_sha256']=row.get('stored_script_sha256')
        row['reviewed']=next((p for p in REVIEWED if p['route']==row.get('operation_uri') and p['method']==row.get('http_method') and p['source_sha256']==row.get('stored_script_sha256')),None)
        row['priority']=100 if row['reviewed'] else 90 if re.search('sign|csr|token',row.get('name',''),re.I) else 50 if 'state-changing operation' in surfaces else 10
        result.append(row)
    return sorted(result,key=lambda r:(-r['priority'],r['operation_uri'],r['sys_id']))

def concrete_path(route,nonce):
    value=re.sub(r'\{([^{}]+)\}',lambda m:quote(nonce if m[1].lower() in ('linkid','token') else '0'*32,safe=''),route)
    p=urlsplit(value)
    if not value.startswith('/api/') or p.scheme or p.netloc or p.query or p.fragment or '{' in value or '}' in value or '\\' in value or '..' in value.split('/'):raise ValueError('Invalid operation route')
    return value

def probes(runtime,record,context,observations):
    inventory=runtime.inventory(record);rows=candidates(observations);nonce='artex-'+uuid.uuid4().hex
    results=[];selected=rows[:10];allowed=set()
    transport=ScopedHTTP(record['origin'],authorized_origins=runtime.store.allowed_origins,methods=('GET','POST','OPTIONS'),allow_path=lambda method,path:(method,path) in allowed)
    cookies={}
    def receive(headers):
        for name,value in headers:
            if name.lower()!='set-cookie':continue
            parsed=SimpleCookie()
            try:parsed.load(value)
            except Exception:continue
            for key,morsel in parsed.items():
                if morsel['domain'] and morsel['domain'].lstrip('.').lower()!=urlsplit(record['origin']).hostname:continue
                path=morsel['path'] or '/'
                if morsel['max-age']=='0':cookies.pop((key,path),None)
                else:cookies[key,path]=morsel.value;inventory._remember(morsel.value)
    allowed.add(('GET','/login.do'))
    guest=transport.request(context,'GET','/login.do',{'Accept':'text/html','User-Agent':'ARTEX-public-rest/1.0'})
    receive(guest['headers'])
    for row in selected:
        context.check();review=row['reviewed'];limitations=[]
        # Re-fetch selected source and route before enabling any actual handler.
        live=read_rows(inventory,context,'sys_ws_operation','sys_id,http_method,operation_uri,default_operation_uri,active,requires_authentication,operation_script','sys_id='+row['sys_id'])
        actual=live['records'][0] if live.get('complete') and len(live['records'])==1 else {}
        source=actual.get('operation_script')
        pinned=bool(review and isinstance(source,str) and hashlib.sha256(source.encode()).hexdigest()==review['source_sha256'] and actual.get('operation_uri')==row['operation_uri'] and (actual.get('default_operation_uri') or actual.get('operation_uri'))==(row.get('default_operation_uri') or row['operation_uri']) and actual.get('http_method')==review['method'] and str(actual.get('active')).lower()=='true' and str(actual.get('requires_authentication')).lower()=='false')
        if not pinned:limitations.append('Source unavailable, changed, or no reviewed handler probe; metadata only')
        for route in dict.fromkeys((row.get('default_operation_uri') or row['operation_uri'],row['operation_uri'])):
            path=concrete_path(route,nonce);method=review['method'] if pinned else 'OPTIONS';body=review['body'] if pinned else None
            allowed.add((method,path))
            headers={'Accept':'application/octet-stream' if '/documents/permalinks/' in path else 'application/json','Accept-Encoding':'identity','User-Agent':'ARTEX-public-rest/1.0','Cache-Control':'no-cache'}
            if body is not None:headers['Content-Type']='application/json'
            for mode in ('none','guest_session'):
                request_headers=dict(headers)
                if mode=='guest_session':
                    cookie='; '.join(key+'='+value for (key,prefix),value in cookies.items() if path==prefix or path.startswith(prefix.rstrip('/')+'/'))
                    if cookie:request_headers['Cookie']=cookie
                response=transport.request(context,method,path,request_headers,body=None if body is None else json.dumps(body,separators=(',',':')).encode())
                if mode=='guest_session':receive(response['headers'])
                selected_headers={k.lower():v for k,v in response['headers'] if k.lower() in ('content-type','allow','www-authenticate','cache-control','x-content-type-options')}
                raw=response['body'];text=runtime.redact(raw[:16384].decode('utf-8','replace'))
                results.append(dict(operation_id=row['sys_id'],source_sha256=row['source_sha256'],method=method,path=path,body=body,authentication=mode,request_header_names=sorted(request_headers),status=response['status'],headers=selected_headers,body_sha256=response['body_sha256'],body_sample=text,body_truncated=len(raw)>16384,limitations=limitations))
    public=[]
    for row in rows:
        public.append({k:v for k,v in row.items() if k not in ('operation_script','reviewed')})
    return dict(resource='public_rest_probes',status=200,records=public,record_count=len(public),selected_ids=[r['sys_id'] for r in selected],requests=results,
        guest_bootstrap={'status':guest['status'],'body_sha256':guest['body_sha256']},coverage='Every discovered public operation classified; highest-priority ten probed',limitations=['Successful existing-object access requires a separately reviewed non-sensitive fixture','No credentialed session or authorization header used in endpoint probes','Operation authentication flags do not establish effective REST ACL or API-policy access'])
