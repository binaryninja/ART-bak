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
    '/api/now/table/sys_api_access_policy','/api/now/table/sys_ws_operation')+tuple('/api/now/table/'+name for name in (
    'sys_script_include','sys_ws_query_parameter_map','sys_user','item_option_new','sc_cat_item','sc_cat_item_producer',
    'sys_one_extend_capability','ds_document_permalink','kb_knowledge','sp_portal'))

def parent(runtime,record):
    rows=runtime.pool.fetch_all(Query("SELECT id,task_id,report->>'run_id' FROM artex_assessments WHERE definition='servicenow.inventory' AND origin=$1 AND credential_ref=$2 AND report IS NOT NULL AND state='done' ORDER BY created_at DESC LIMIT 1",(record['origin'],record['credential_ref'])))
    return dict(zip(('id','task_id','run_id'),rows[0])) if rows else None

def inherited(runtime,record,resource):
    source=parent(runtime,record)
    if not source:return None
    value=runtime.store.observations(source['id']).get(resource)
    return dict(value,source_assessment=source) if value is not None else None

def read_rows(inventory,context,table,fields,query,*,max_records=10000,page_size=250):
    records=[];pages=[]
    for offset in range(0,max_records,page_size):
        limit=min(page_size,max_records-offset)
        path='/api/now/table/'+table+'?'+urlencode(dict(sysparm_query=query+'^ORDERBYsys_id',sysparm_fields=fields,sysparm_limit=limit,sysparm_offset=offset,sysparm_exclude_reference_link='true'))
        response=json.loads(inventory.read_registered(context,path,max_body_bytes=1048576));pages.append(dict(path=path,**{k:response.get(k) for k in ('status','body_sha256','truncated')}))
        if response.get('status')!=200 or response.get('truncated'):return dict(records=records,pages=pages,complete=False,limitation='metadata unavailable or bounded')
        value=json.loads(response['body']).get('result')
        if not isinstance(value,list):raise ValueError('Unexpected metadata response')
        records.extend(value)
        if len(value)<limit:return dict(records=records,pages=pages,complete=True)
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

def concrete_path(route,nonce,params=None,query=None):
    params=params or {}
    value=re.sub(r'\{([^{}]+)\}',lambda m:quote(str(params.get(m[1],nonce if m[1].lower() in ('linkid','token') else '0'*32)),safe=''),route)
    p=urlsplit(value)
    if not value.startswith('/api/') or p.scheme or p.netloc or p.query or p.fragment or '{' in value or '}' in value or '\\' in value or '..' in value.split('/'):raise ValueError('Invalid operation route')
    return value+('?' + urlencode(query) if query else '')

def response_outcome(case,status,text):
    try:data=json.loads(text)
    except ValueError:data={}
    if status==403 and 'Failed API level ACL Validation' in text:return 'api_acl_denied'
    expected=case.get('expected',{})
    if status in expected.get('status',[]) and (not expected.get('text') or expected['text'].lower() in text.lower()):
        if status==200 and isinstance(data,dict) and ('error' in data or isinstance(data.get('result'),dict) and data['result'].get('success') is False) and not expected.get('text'):return 'handler_rejected'
        result=data.get('result') if isinstance(data,dict) else None
        kind=expected.get('result')
        if kind=='facets' and not isinstance(result,(list,dict)):return 'unexpected_response'
        if kind=='address' and not isinstance(result,dict):return 'unexpected_response'
        if kind in ('dynamic_value','empty_dynamic_value'):
            value=result.get(expected.get('field')) if isinstance(result,dict) else None
            if not isinstance(value,dict) or 'value' not in value:return 'unexpected_response'
            if kind=='empty_dynamic_value' and value['value']!='':return 'unexpected_response'
            if kind=='dynamic_value' and str(value['value']).lower() not in ('true','false','1','0'):return 'handler_rejected'
        return 'expected_handler_result'
    return 'unexpected_response'

def probes(runtime,record,context,observations):
    from .servicenow_source import verified,FIELDS
    inventory=runtime.inventory(record);rows=candidates(observations);nonce='artex-'+uuid.uuid4().hex
    analysis=observations.get('public_rest_source',{})
    if not verified(runtime,record,analysis):return dict(resource='public_rest_probes',status=0,error='verified_source_plan_unavailable',records=[],requests=[])
    planned={row['operation_id']:row for row in analysis['records']}
    selected=[row for row in rows if row['sys_id'] in planned];results=[];skipped=[];allowed=set();cookies={};helper_cache={}
    transport=ScopedHTTP(record['origin'],authorized_origins=runtime.store.allowed_origins,methods=('GET','POST','OPTIONS'),allow_path=lambda method,path:(method,path) in allowed)
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
    def helper_current(name,digest):
        if (name,digest) not in helper_cache:
            value=read_rows(inventory,context,'sys_script_include','sys_id,api_name,script','api_name='+name,max_records=2,page_size=2)
            found=value['records'];source=found[0].get('script') if value['complete'] and len(found)==1 else None
            helper_cache[name,digest]=isinstance(source,str) and hashlib.sha256(source.encode()).hexdigest()==digest
        return helper_cache[name,digest]
    allowed.add(('GET','/login.do'))
    guest=transport.request(context,'GET','/login.do',{'Accept':'text/html','User-Agent':'ARTEX-public-rest/2.0'});receive(guest['headers'])
    for row in selected:
        context.check();plan=planned[row['sys_id']]
        live=read_rows(inventory,context,'sys_ws_operation',FIELDS,'sys_id='+row['sys_id'],max_records=2,page_size=2)
        actual=live['records'][0] if live.get('complete') and len(live['records'])==1 else {}
        source=actual.get('operation_script');review=next((p for p in REVIEWED if p['route']==actual.get('operation_uri') and p['method']==actual.get('http_method') and p['source_sha256']==plan.get('source_sha256')),None)
        pinned=bool(review and isinstance(source,str) and hashlib.sha256(source.encode()).hexdigest()==plan['source_sha256'] and actual.get('operation_uri')==row['operation_uri'] and (actual.get('default_operation_uri') or actual.get('operation_uri'))==(row.get('default_operation_uri') or row['operation_uri']) and str(actual.get('active')).lower()=='true' and str(actual.get('requires_authentication')).lower()=='false' and all(helper_current(n,d) for n,d in plan.get('helper_pins',{}).items()))
        cases=plan['cases'] if pinned else []
        if not pinned and plan['cases']:skipped.extend(dict(case_id=c['case_id'],reason='Source or helper changed after plan creation') for c in plan['cases'])
        if not cases:cases=[dict(case_id=row['sys_id']+':metadata',label='metadata-only',description='Protected or unreviewed handler',method='OPTIONS',path_params={},body=None,query={},expected={'status':[200,204,405]},source_evidence=None,safety='metadata',allow_control=False)]
        routes=list(dict.fromkeys((row.get('default_operation_uri') or row['operation_uri'],row['operation_uri'])))
        for case in cases:
            for index,route in enumerate(routes):
                path=concrete_path(route,nonce,case['path_params'],case['query']);method=case['method'];body=case['body'];allowed.add((method,path))
                modes=['none','guest_session']+(['assessment_session'] if index==0 and case['allow_control'] and record.get('credential_ref') else [])
                for mode in modes:
                    headers={'Accept':'application/octet-stream' if '/documents/permalinks/' in path else 'application/json','Accept-Encoding':'identity','User-Agent':'ARTEX-public-rest/2.0','Cache-Control':'no-cache'}
                    if body is not None:headers['Content-Type']='application/json'
                    if mode=='guest_session':
                        cookie='; '.join(key+'='+value for (key,prefix),value in cookies.items() if urlsplit(path).path==prefix or urlsplit(path).path.startswith(prefix.rstrip('/')+'/'))
                        if cookie:headers['Cookie']=cookie
                    elif mode=='assessment_session':
                        if not inventory._session.login(context):
                            skipped.append(dict(case_id=case['case_id'],authentication=mode,reason='Credentialed control session unavailable'));continue
                        headers=inventory._session.headers(path,headers)
                    response=transport.request(context,method,path,headers,body=None if body is None else json.dumps(body,separators=(',',':')).encode())
                    if mode=='guest_session':receive(response['headers'])
                    elif mode=='assessment_session':inventory._session.receive(path,response['headers'])
                    raw=response['body'];text=runtime.redact(raw[:16384].decode('utf-8','replace'))
                    selected_headers={k.lower():v for k,v in response['headers'] if k.lower() in ('content-type','allow','www-authenticate','cache-control','x-content-type-options')}
                    results.append(dict(operation_id=row['sys_id'],case_id=case['case_id'],label=case['label'],purpose=case['description'],source_sha256=plan.get('source_sha256'),source_evidence=case['source_evidence'],safety=case['safety'],fixture_refs=case.get('fixture_refs',[]),method=method,path=path,body=body,authentication=mode,request_header_names=sorted(headers),status=response['status'],headers=selected_headers,body_sha256=response['body_sha256'],body_sample=text,body_truncated=len(raw)>16384,expected=case['expected'],outcome=response_outcome(case,response['status'],text),limitations=plan['limitations']))
    public=[{k:v for k,v in row.items() if k not in ('operation_script','reviewed')} for row in rows]
    return dict(resource='public_rest_probes',status=200,records=public,record_count=len(public),selected_ids=[r['sys_id'] for r in selected],requests=results,skipped=skipped,source_plan_signature=analysis['plan_signature'],guest_bootstrap={'status':guest['status'],'body_sha256':guest['body_sha256']},coverage='Source-derived cases with anonymous/guest requests and scoped administrator controls',limitations=['Effectful success paths remain outside the read-only definition','Native/protected source and missing fixtures are recorded per operation','API ACL denial does not establish handler-level authorization'])
