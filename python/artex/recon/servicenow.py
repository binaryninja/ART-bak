"""Read-only inventory for the one explicitly authorized developer instance."""
import base64
import hashlib
import html
import http.client
import json
import re
import ssl
import threading
import time
from urllib.parse import urlencode

HOST = 'dev366312.service-now.com'
ORIGIN = 'https://' + HOST
TOOL_NAME = 'inspect_servicenow'
TABLES = {
    'current_user': ('sys_user', 'sys_id,user_name,active', 'user_name={username}'),
    'api_definitions': ('sys_ws_definition', 'sys_id,name,namespace,service_id,active', ''),
    'api_resources': ('sys_ws_operation', 'name,http_method,relative_path,web_service_definition', ''),
    'tables': ('sys_db_object', 'name,label,super_class,sys_scope', 'ORDERBYname'),
    'applications': ('sys_app', 'name,version,scope,active', ''),
    'public_pages': ('sys_public', 'page,active', 'active=true'),
}
PAGES = {'home': '/', 'login': '/login.do', 'robots': '/robots.txt', 'stats': '/stats.do'}
RESOURCES = tuple(PAGES) + tuple(TABLES)
SCHEMA = {'type': 'object', 'properties': {'resource': {'type': 'string', 'enum': list(RESOURCES)}},
          'required': ['resource'], 'additionalProperties': False}


def valid_input(value):
    return type(value) is dict and set(value) == {'resource'} and value['resource'] in RESOURCES


class ServiceNowInventory:
    def __init__(self, username, password, *, interval=1.0, connection_factory=http.client.HTTPSConnection):
        if not re.fullmatch(r'[A-Za-z0-9_.@-]{1,128}', username) or not password or any(ord(c) < 32 for c in password):
            raise ValueError('Explicit instance credential required')
        if interval < 1:
            raise ValueError('At most one target request per second')
        self.username = username
        self._auth = 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
        self._secrets = (password, self._auth, self._auth[6:])
        self._interval, self._connection = interval, connection_factory
        self._lock, self._last = threading.Lock(), 0.0
        self._authentication_denied = False
        self._tls = ssl.create_default_context()

    def redact(self, value):
        for secret in self._secrets:
            value = value.replace(secret, '[redacted]')
        return value

    def path(self, resource):
        if resource in PAGES:
            return PAGES[resource]
        if resource not in TABLES:
            raise ValueError('Unknown inventory operation')
        table, fields, query = TABLES[resource]
        params = {'sysparm_fields': fields, 'sysparm_limit': '1' if resource == 'current_user' else '100',
                  'sysparm_exclude_reference_link': 'true', 'sysparm_display_value': 'false'}
        if query:
            params['sysparm_query'] = query.format(username=self.username)
        return '/api/now/table/' + table + '?' + urlencode(params)

    def inspect(self, context, arguments):
        if not valid_input(arguments):
            raise ValueError('Named inventory operation required')
        resource = arguments['resource']
        path = self.path(resource)
        with self._lock:
            context.check()
            authenticated = resource in TABLES or resource == 'stats'
            if authenticated and self._authentication_denied:
                return json.dumps({'resource': resource, 'path': path, 'status': 0,
                                   'requested': False, 'error': 'authentication_denied_before_request'})
            remaining = self._last + self._interval - time.monotonic()
            while remaining > 0:
                time.sleep(min(remaining, 0.05))
                context.check()
                remaining = self._last + self._interval - time.monotonic()
            self._last = time.monotonic()
            headers = {'Accept': 'application/json' if resource in TABLES else 'text/html,text/plain',
                       'Accept-Encoding': 'identity', 'User-Agent': 'ARTEX-scoped-recon/1.0'}
            if authenticated:
                headers['Authorization'] = self._auth
            conn = self._connection(HOST, 443, timeout=min(20, context.remaining_seconds()), context=self._tls)
            try:
                context.check()
                conn.request('GET', path, headers=headers)
                response = conn.getresponse()
                if authenticated and response.status == 401:
                    self._authentication_denied = True
                body = response.read(262145)
                context.check()
                if len(body) > 262144:
                    return json.dumps({'resource': resource, 'status': response.status, 'error': 'response_limit'})
                result = {'resource': resource, 'path': path, 'status': response.status,
                          'body_sha256': hashlib.sha256(body).hexdigest(),
                          'content_type': response.getheader('Content-Type', '').split(';')[0],
                          'security_headers': {name: response.getheader(name) for name in
                              ('Strict-Transport-Security', 'X-Content-Type-Options', 'X-Frame-Options')
                              if response.getheader(name)}}
                # No redirects, cookies, arbitrary headers, scripts, or raw HTML.
                if resource in TABLES and response.status == 200:
                    data = json.loads(body)
                    rows = data.get('result')
                    if not isinstance(rows, list) or len(rows) > 100:
                        raise ValueError('Unexpected inventory result')
                    fields = TABLES[resource][1].split(',')
                    result['records'] = [{k: str(row.get(k, ''))[:512] for k in fields} for row in rows if isinstance(row, dict)]
                    result['limit'] = 1 if resource == 'current_user' else 100
                elif resource in PAGES and response.status == 200:
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
                return self.redact(json.dumps(result, sort_keys=True))
            finally:
                conn.close()
