"""Read-only inventory for the one explicitly authorized developer instance."""
import base64
import hashlib
import html
import http.client
import json
import re
import threading
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit
from .servicenow_session import LoginSession
from ..tools.http import ScopedHTTP

HOST = 'dev366312.service-now.com'
ORIGIN = 'https://' + HOST
TOOL_NAME = 'inspect_servicenow'
TABLES = {
    'roles': ('sys_user_has_role', 'role.name', 'user.user_name={username}'),
    'current_user': ('sys_user', 'sys_id,user_name,active', 'user_name={username}'),
    'api_definitions': ('sys_ws_definition', 'sys_id,name,namespace,service_id,base_uri,active,is_versioned,default_version,enforce_acl,sys_scope', ''),
    'api_resources': ('sys_ws_operation', 'sys_id,name,active,http_method,relative_path,operation_uri,default_operation_uri,web_service_definition,web_service_version,requires_authentication,requires_acl_authorization,requires_snc_internal_role,enforce_acl', ''),
    'tables': ('sys_db_object', 'name,label,super_class,sys_scope', 'ORDERBYname'),
    'applications': ('sys_app', 'name,version,scope,active', ''),
    'public_pages': ('sys_public', 'page,active', 'active=true'),
}
PAGES = {'home': '/', 'login': '/login.do', 'robots': '/robots.txt', 'stats': '/stats.do'}
RESOURCES = tuple(PAGES) + tuple(k for k in TABLES if k != 'roles')
TABLES.update({
    'application_scopes':('sys_scope','sys_id,name,scope,version,private',''),
    'delegated_developers':('sys_user_has_role','user.user_name,role.name','role.nameINdelegated_developer,app_engine_admin,itil_admin'),
    'integration_metadata':('sys_rest_message','sys_id,name,sys_scope,authentication_type',''),
    'acl_definitions':('sys_security_acl','sys_id,name,type,operation,active,admin_overrides','active=true'),
    'acl_roles':('sys_security_acl_role','sys_security_acl.name,sys_user_role.name',''),
    'role_inheritance':('sys_user_role_contains','role.name,contains.name',''),
    'api_authentication':('sys_ws_operation','sys_id,name,http_method,operation_uri,relative_path,web_service_definition,web_service_version,requires_authentication,requires_acl_authorization,requires_snc_internal_role,enforce_acl',''),
    'security_properties':('sys_properties','name,value','nameINglide.security.strict_update_check,glide.security.use_csrf_token,glide.security.csrf.strict.validation.mode,glide.ui.escape_all_script,glide.security.url.whitelist,glide.authenticate.multifactor'),
    'api_versions':('sys_ws_version','sys_id,web_service_definition,version,version_id,active,is_default,deprecated',''),
    'api_scripts':('sys_ws_operation','sys_id,name,http_method,operation_uri,web_service_definition,web_service_version,requires_authentication,requires_acl_authorization,requires_snc_internal_role,enforce_acl,operation_script',''),
    'data_broker_types':('sys_db_object','sys_id,name,label,super_class','nameSTARTSWITHsys_ux_data_broker'),
    'data_brokers':('sys_ux_data_broker','sys_id,name,api_name,sys_class_name,sys_scope,private,schema_version',''),
    'data_broker_rest':('sys_ux_data_broker_rest','sys_id,name,api_name,sys_scope,private,mutates_server_data,http_method,api_url',''),
    'data_broker_external_rest':('sys_ux_data_broker_rest_external','sys_id,name,api_name,sys_scope,private,mutates_server_data,http_method,endpoint',''),
    'data_broker_graphql':('sys_ux_data_broker_graphql','sys_id,name,api_name,sys_scope,private,mutates_server_data,type,use_record_domain',''),
})
ALL_RESOURCES = tuple(PAGES) + ('current_user', 'roles') + tuple(k for k in TABLES if k not in ('current_user','roles'))
SCHEMA = {'type': 'object', 'properties': {'resource': {'type': 'string', 'enum': list(ALL_RESOURCES)}},
          'required': ['resource'], 'additionalProperties': False}

LARGE_RESOURCES=frozenset(k for k in TABLES if k.startswith(('api_','data_broker')))
MAX_PAGE_BYTES=1048576

@dataclass(frozen=True)
class EnumerationPolicy:
    page_size:int=250
    max_records:int=10000
    large_max_records:int=50000
    max_result_bytes:int=67108864

    def __post_init__(self):
        for value,minimum,maximum in ((self.page_size,1,500),(self.max_records,1,1000000),(self.large_max_records,1,1000000),(self.max_result_bytes,1048576,268435456)):
            if type(value) is not int or not minimum<=value<=maximum:raise ValueError('Invalid inventory enumeration limit')

    def record_limit(self,resource):
        return 1 if resource=='current_user' else self.large_max_records if resource in LARGE_RESOURCES else self.max_records

    def page_limit(self,resource):
        return min(self.page_size,25 if resource=='api_scripts' else self.page_size,self.record_limit(resource))

    def public(self,resource):
        limit=self.record_limit(resource);size=self.page_limit(resource)
        return dict(page_size=size,max_records=limit,max_pages=limit if resource=='api_scripts' else (limit+size-1)//size,max_result_bytes=self.max_result_bytes)

DEFAULT_ENUMERATION=EnumerationPolicy()


def valid_input(value):
    return type(value) is dict and set(value) == {'resource'} and value['resource'] in ALL_RESOURCES


class ServiceNowInventory:
    def __init__(self, username="", password="", *, origin=ORIGIN, authorized_origins=(ORIGIN,), interval=1.0, connection_factory=http.client.HTTPSConnection,extra_read_paths=(),enumeration=DEFAULT_ENUMERATION):
        parsed=urlsplit(origin)
        if origin not in authorized_origins or parsed.scheme!="https" or not parsed.hostname or parsed.netloc!=parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Explicit approved HTTPS instance origin required")
        self.origin,self.host=origin,parsed.hostname
        if type(enumeration) is not EnumerationPolicy:raise ValueError('Explicit enumeration policy required')
        self.enumeration=enumeration
        if not isinstance(username,str) or not isinstance(password,str) or len(password.encode())>4096 or bool(username)!=bool(password) or username and (not re.fullmatch(r'[A-Za-z0-9_.@-]{1,128}', username) or any(ord(c) < 32 for c in password)):
            raise ValueError('Explicit instance credential required')
        if interval < 1:
            raise ValueError('At most one target request per second')
        self.username = username
        self._auth = ('Basic ' + base64.b64encode((username + ':' + password).encode()).decode()) if username else ''
        self._secrets = [x for x in (password, self._auth, self._auth[6:]) if x]
        self._lock = threading.Lock()
        self._authentication_denied = False
        self._basic_denied = False
        self._extra_read_paths=frozenset(extra_read_paths)
        if any(not isinstance(p,str) or not p.startswith('/') or urlsplit(p).path!=p or p.startswith('//') or '\\' in p for p in self._extra_read_paths):raise ValueError('Exact extension paths required')
        allowed_paths=set(PAGES.values())|{'/login_redirect.do','/navpage.do','/welcome.do'}|{'/api/now/table/'+row[0] for row in TABLES.values()}|self._extra_read_paths
        self._http=ScopedHTTP(origin,authorized_origins=authorized_origins,methods=('GET','POST'),
            allow_path=lambda method,path:(method=='GET' and urlsplit(path).path in allowed_paths) or (method=='POST' and path=='/login.do'),
            interval=interval,max_bytes=MAX_PAGE_BYTES,connection_factory=connection_factory)
        self._session = LoginSession(origin,username,password,self._request,self._remember)

    def _remember(self, secret):
        if secret and secret not in self._secrets:self._secrets.append(secret)

    def _request(self, context, method, path, headers, *, body=None, session=False):
        if session:headers=self._session.headers(path,headers)
        response=self._http.request(context,method,path,headers,body=body)
        if session:self._session.receive(path,response['headers'])
        return response

    def redact(self, value):
        for secret in self._secrets:
            value = value.replace(secret, '[redacted]')
        return value

    def read_registered(self,context,path):
        if urlsplit(path).path not in self._extra_read_paths:raise PermissionError('Unregistered authenticated HTTP path')
        with self._lock:
            context.check()
            if not self._auth or self._authentication_denied:return json.dumps({'status':0,'error':'credential_unavailable','requested':False})
            headers={'Accept':'application/json,text/plain','Accept-Encoding':'identity'}
            if not self._basic_denied:
                response=self._request(context,'GET',path,dict(headers,Authorization=self._auth))
                if response['status']==401:self._basic_denied=True
            if self._basic_denied:
                if not self._session.login(context):return json.dumps({'status':0,'error':'session_authentication_unavailable'})
                response=self._request(context,'GET',path,headers,session=True)
            body=response['body']
            return self.redact(json.dumps({'status':response['status'],'body_sha256':response['body_sha256'],'body':body[:65536].decode('utf-8','replace'),'truncated':len(body)>65536,'authentication_method':'session' if self._basic_denied else 'basic'}))

    def path(self, resource, offset=0, limit=None):
        if resource in PAGES:
            return PAGES[resource]
        if resource not in TABLES:
            raise ValueError('Unknown inventory operation')
        table, fields, query = TABLES[resource]
        limit=min(self.enumeration.page_limit(resource),self.enumeration.record_limit(resource)-offset) if limit is None else limit
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=self.enumeration.page_size or offset+limit>self.enumeration.record_limit(resource):raise ValueError('Invalid inventory page')
        params = {'sysparm_fields': fields, 'sysparm_limit': str(limit),
                  'sysparm_exclude_reference_link': 'true', 'sysparm_display_value': 'false'}
        if query:
            params['sysparm_query'] = query.format(username=self.username)
        if resource!='current_user':
            params['sysparm_query']=params.get('sysparm_query','')+('^' if params.get('sysparm_query') else '')+'ORDERBYsys_id'
            if offset:params['sysparm_offset']=str(offset)
        return '/api/now/table/' + table + '?' + urlencode(params)

    def inspect(self, context, arguments):
        if not valid_input(arguments):
            raise ValueError('Named inventory operation required')
        resource = arguments['resource']
        def read_page(offset):
            limit=min(self.enumeration.page_limit(resource),self.enumeration.record_limit(resource)-offset) if resource in TABLES else None
            attempts=[]
            while True:
                page=self._inspect_page(context,resource,offset,limit)
                if resource!='api_scripts' or page.get('error')!='response_limit' or limit==1:
                    if attempts:page['page_retries']=attempts
                    return page
                attempts.append({'limit':limit,'status':page['status'],'body_sha256':page.get('body_sha256'),'error':'response_limit'})
                limit=max(1,limit//2)
        result=read_page(0)
        if resource not in TABLES or result.get('status')!=200 or 'records' not in result:return self.redact(json.dumps(result,sort_keys=True))
        def receipt(page,offset):return {'offset':offset,'limit':page.get('limit'),'status':page.get('status'),'body_sha256':page.get('body_sha256'),'records':len(page.get('records',[])),**({'retries':page['page_retries']} if page.get('page_retries') else {})}
        result['pages']=[receipt(result,0)]
        offset=result['limit'];size=len(json.dumps(result).encode());cap=self.enumeration.record_limit(resource)
        while result.get('more_records_possible') and offset<cap:
            page=read_page(offset)
            result['pages'].append(receipt(page,offset))
            if page.get('status')!=200 or 'records' not in page:
                result['pagination_error']=page.get('error','HTTP '+str(page.get('status')));break
            size+=len(json.dumps(page['records']).encode())+len(json.dumps(result['pages'][-1]).encode())
            if size>self.enumeration.max_result_bytes:
                result['pagination_error']='aggregate_response_limit';break
            result['records'].extend(page['records']);result['more_records_possible']=page['more_records_possible'];offset+=page['limit']
            if page.get('fields_truncated'):result['fields_truncated']=True
        result['record_count']=len(result['records']);result.update(self.enumeration.public(resource))
        if result.get('more_records_possible') and 'pagination_error' not in result:result['pagination_error']='record_limit'
        result['completeness']='bounded' if result.get('more_records_possible') else 'complete'
        return self.redact(json.dumps(result,sort_keys=True))

    def _inspect_page(self,context,resource,offset,limit=None):
        path = self.path(resource,offset,limit)
        with self._lock:
            context.check()
            authenticated = resource in TABLES or resource == 'stats' and bool(self._auth)
            if authenticated and (self._authentication_denied or not self._auth):
                return {'resource': resource, 'path': path, 'status': 0,
                                   'requested': False, 'error': 'authentication_denied_before_request' if self._authentication_denied else 'credentials_not_supplied'}
            headers = {'Accept': 'application/json' if resource in TABLES else 'text/html,text/plain',
                       'Accept-Encoding': 'identity', 'User-Agent': 'ARTEX-scoped-recon/1.0'}
            if authenticated and not self._basic_denied:
                response=self._request(context,'GET',path,dict(headers,Authorization=self._auth))
                if response['status']==401:self._basic_denied=True
            elif not authenticated:response=self._request(context,'GET',path,headers)
            if authenticated and self._basic_denied:
                if not self._session.login(context):
                    return dict(resource=resource,path=path,status=0,requested=False,error='session_authentication_unavailable',basic_authentication_denied=True)
                response=self._request(context,'GET',path,headers,session=True)
                if resource=='current_user' and response['status']==401:self._authentication_denied=True
            status=response['status'];body=response['body']
            response_headers={k.lower():v for k,v in response['headers']}
            def header(name,default=None):return response_headers.get(name.lower(),default)
            if len(body) > MAX_PAGE_BYTES:
                return {'resource': resource, 'status': status, 'error': 'response_limit','body_sha256':response['body_sha256'],'limit':limit}
            result = {'resource': resource, 'path': path, 'status': status,
                      'body_sha256': hashlib.sha256(body).hexdigest(),
                      'content_type': header('Content-Type', '').split(';')[0],
                      'security_headers': {name: header(name) for name in
                          ('Strict-Transport-Security', 'X-Content-Type-Options', 'X-Frame-Options')
                          if header(name)}}
            if authenticated:result['authentication_method']='session' if self._basic_denied else 'basic'
            # Inventory responses never expose redirects, cookies, or raw HTML.
            if resource in TABLES and status == 200:
                data = json.loads(body)
                rows = data.get('result')
                limit=min(self.enumeration.page_limit(resource),self.enumeration.record_limit(resource)-offset) if limit is None else limit
                if not isinstance(rows, list) or len(rows) > limit or any(not isinstance(row,dict) for row in rows):
                    raise ValueError('Unexpected inventory result')
                fields = TABLES[resource][1].split(',')
                def field(row,key):
                    if key=='operation_script':return self.redact(row[key] if isinstance(row.get(key),str) else '')
                    value=str(row.get(key,''));maximum=4096 if key in ('base_uri','operation_uri','default_operation_uri','relative_path','api_url','endpoint') else 512
                    if len(value)>maximum:result['fields_truncated']=True
                    return value[:maximum]
                result['records'] = [{k:field(row,k) for k in fields} for row in rows]
                if resource=='api_scripts':
                    for raw,stored in zip(rows,result['records']):
                        available=isinstance(raw.get('operation_script'),str)
                        source=raw.get('operation_script','') if available else ''
                        stored.update(operation_script_available=available,operation_script_bytes=len(source.encode()),operation_script_sha256=hashlib.sha256(source.encode()).hexdigest() if available else None,stored_script_sha256=hashlib.sha256(stored['operation_script'].encode()).hexdigest() if available else None,operation_script_redacted=source!=stored['operation_script'])
                result['limit']=limit
                total=header('X-Total-Count','')
                total=int(total) if total.isascii() and total.isdigit() and len(total)<=10 else None
                if total is not None:result['total_records_reported']=total
                result['more_records_possible'] = resource!='current_user' and (offset+limit<total if total is not None else bool(re.search(r'rel=["\']?next\b',header('Link',''))) or len(rows)==limit)
            elif resource in PAGES and status == 200:
                text = body.decode('utf-8', 'replace')
                title = re.search(r'<title\b[^>]*>(.*?)</title>', text, re.I | re.S)
                if title:
                    result['title'] = html.unescape(re.sub('<[^>]+>', '', title.group(1)))[:256]
                if resource == 'robots':
                    result['directives'] = [line[:256] for line in text.splitlines()
                                            if re.match(r'(?i)^(user-agent|allow|disallow|sitemap):', line)][:80]
                if resource == 'stats':
                    plain = html.unescape(re.sub('<[^>]+>', ' ', text))
                    result['build_metadata'] = re.findall(r'(?i)(?:Build (?:name|date|tag)|Version)\s*[:=]\s*[^\r\n<]{1,160}', plain)[:8]
            return result
