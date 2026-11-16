"""Read-only intercept tool configuration; no routes installed or runtime changes.

Source: server/intercept.go interceptGetToolConfig; intercept/intercept.go
GetEnabledTools. Missing uses ordered defaults; malformed JSON/type yields [];
JSON null remains null; null string-array elements become empty strings as Go.
Unknown names, duplicates and order are preserved. Oversize is a redacted error,
not a truncated policy. This metadata does not grant execution permission.
"""
import json
from urllib.parse import urlsplit
from ..db.core import Query
from ..intercept import DEFAULT_ENABLED_TOOLS
from .core import json_response,error_response

class ConfigUnavailable(RuntimeError):pass
class InvalidRequest(ValueError):pass

class InterceptConfigReader:
    def __init__(self,executor,*,max_bytes=65536,max_tools=1000):
        if not callable(getattr(executor,'fetch_all',None)):raise ValueError('Explicit executor required')
        if type(max_bytes) is not int or not 1<=max_bytes<=1048576 or type(max_tools) is not int or not 1<=max_tools<=10000:raise ValueError('Finite bounds required')
        self.executor=executor;self.max_bytes=max_bytes;self.max_tools=max_tools
        self._pins=(executor,max_bytes,max_tools)
    def validate(self):
        e,b,n=self._pins
        if self.executor is not e or self.max_bytes!=b or self.max_tools!=n:raise ConfigUnavailable()
    def read(self):
        self.validate()
        rows=self._pins[0].fetch_all(Query('SELECT CASE WHEN octet_length(value)<=$2 THEN value ELSE NULL END FROM settings WHERE key=$1',('intercept_enabled_tools',self.max_bytes)))
        self.validate()
        if type(rows) not in (list,tuple) or len(rows)>1:raise ConfigUnavailable()
        if not rows:return list(DEFAULT_ENABLED_TOOLS)
        row=rows[0]
        if type(row) not in (list,tuple) or len(row)!=1 or type(row[0]) is not str:raise ConfigUnavailable()
        if len(row[0].encode('utf-8'))>self.max_bytes:raise ConfigUnavailable()
        try:value=json.loads(row[0],parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,RecursionError):return []
        if value is None:return None
        if type(value) is not list or any(v is not None and type(v) is not str for v in value):return []
        if len(value)>self.max_tools:raise ConfigUnavailable()
        # JSON's decoder combines valid UTF-16 escape pairs already. Go replaces
        # remaining unpaired surrogate escapes rather than returning invalid Unicode.
        return ['' if v is None else ''.join('\ufffd' if 0xD800<=ord(c)<=0xDFFF else c for c in v) for v in value]

class InterceptConfigReadService:
    def __init__(self,reader,*,authorize,current,lifecycle_guard):
        if type(reader) is not InterceptConfigReader or any(not callable(v) for v in (authorize,current,lifecycle_guard)):raise ValueError('Explicit read authority required')
        reader.validate();self.reader=reader;self.authorize=authorize;self.current=current;self.guard=lifecycle_guard
        self._pins=(reader,reader.executor,authorize,current,lifecycle_guard)
    def _check(self):
        r,e,a,c,g=self._pins
        if self.reader is not r or r.executor is not e or self.authorize is not a or self.current is not c or self.guard is not g:raise ConfigUnavailable()
        r.validate()
    def handle(self,request):
        # Caller must mount behind its original authentication authority. This
        # module intentionally supplies no register()/application factory path.
        try:
            if request.method!='GET' or request.path!='/api/intercept/tool-config' or request.body or urlsplit(request.target).query:raise InvalidRequest()
            def allow():
                self._check()
                if self._pins[3]() is not True:raise PermissionError()
                self._check()
                if self._pins[2](request) is not True:raise PermissionError()
                self._check()
                if self._pins[3]() is not True:raise PermissionError()
                self._check()
            allow()
            with self._pins[4]():
                allow();value=self._pins[0].read();allow()
                response=json_response(200,{'enabled_tools':value});allow()
            allow();return response
        except PermissionError:return error_response(403,'Intercept configuration read denied')
        except InvalidRequest:return error_response(400,'Invalid intercept configuration request')
        except Exception:return error_response(500,'Intercept configuration unavailable')
