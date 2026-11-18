"""Read-only inventory for the one explicitly authorized developer instance."""
import base64
import hashlib
import html
import http.client
import json
import re
import threading
from urllib.parse import urlencode, urlsplit
from .servicenow_session import LoginSession
from ..tools.http import ScopedHTTP

HOST = 'dev366312.service-now.com'
ORIGIN = 'https://' + HOST
TOOL_NAME = 'inspect_servicenow'
TABLES = {
    'roles': ('sys_user_has_role', 'role.name', 'user.user_name={username}'),
    'current_user': ('sys_user', 'sys_id,user_name,active', 'user_name={username}'),
    'api_definitions': ('sys_ws_definition', 'sys_id,name,namespace,service_id,active', ''),
    'api_resources': ('sys_ws_operation', 'name,http_method,relative_path,web_service_definition', ''),
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
    'api_authentication':('sys_ws_operation','name,http_method,relative_path,web_service_definition,requires_authentication,requires_acl_authorization',''),
    'security_properties':('sys_properties','name,value','nameINglide.security.strict_update_check,glide.security.use_csrf_token,glide.security.csrf.strict.validation.mode,glide.ui.escape_all_script,glide.security.url.whitelist,glide.authenticate.multifactor'),
})
ALL_RESOURCES = tuple(PAGES) + ('current_user', 'roles') + tuple(k for k in TABLES if k not in ('current_user','roles'))
SCHEMA = {'type': 'object', 'properties': {'resource': {'type': 'string', 'enum': list(ALL_RESOURCES)}},
          'required': ['resource'], 'additionalProperties': False}


def valid_input(value):
    return type(value) is dict and set(value) == {'resource'} and value['resource'] in ALL_RESOURCES


class ServiceNowInventory:
    def __init__(self, username="", password="", *, origin=ORIGIN, authorized_origins=(ORIGIN,), interval=1.0, connection_factory=http.client.HTTPSConnection,extra_read_paths=()):
        parsed=urlsplit(origin)
        if origin not in authorized_origins or parsed.scheme!="https" or not parsed.hostname or parsed.netloc!=parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Explicit approved HTTPS instance origin required")
        self.origin,self.host=origin,parsed.hostname
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
            interval=interval,connection_factory=connection_factory)
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

    def path(self, resource, offset=0):
        if resource in PAGES:
            return PAGES[resource]
        if resource not in TABLES:
            raise ValueError('Unknown inventory operation')
        table, fields, query = TABLES[resource]
        params = {'sysparm_fields': fields, 'sysparm_limit': '1' if resource == 'current_user' else '100',
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
        result=self._inspect_page(context,resource,0)
        if resource not in TABLES or result.get('status')!=200 or 'records' not in result:return self.redact(json.dumps(result,sort_keys=True))
        result['pages']=[{'offset':0,'status':result['status'],'body_sha256':result['body_sha256'],'records':len(result['records'])}]
        while result.get('more_records_possible') and len(result['pages'])<5:
            offset=len(result['records']);page=self._inspect_page(context,resource,offset)
            result['pages'].append({'offset':offset,'status':page.get('status'),'body_sha256':page.get('body_sha256'),'records':len(page.get('records',[]))})
            if page.get('status')!=200 or 'records' not in page:
                result['pagination_error']=page.get('error','HTTP '+str(page.get('status')));break
            if len(json.dumps(result).encode())+len(json.dumps(page['records']).encode())>524288:
                result['pagination_error']='aggregate_response_limit';break
            result['records'].extend(page['records']);result['more_records_possible']=page['more_records_possible']
        result['record_count']=len(result['records']);result['max_pages']=5
        result['completeness']='bounded' if result.get('more_records_possible') else 'complete'
        return self.redact(json.dumps(result,sort_keys=True))

    def _inspect_page(self,context,resource,offset):
        path = self.path(resource,offset)
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
            if len(body) > 262144:
                return {'resource': resource, 'status': status, 'error': 'response_limit'}
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
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError('Unexpected inventory result')
                fields = TABLES[resource][1].split(',')
                result['records'] = [{k: str(row.get(k, ''))[:512] for k in fields} for row in rows if isinstance(row, dict)]
                result['limit'] = 1 if resource == 'current_user' else 100
                result['more_records_possible'] = resource != 'current_user' and len(rows) == result['limit']
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
