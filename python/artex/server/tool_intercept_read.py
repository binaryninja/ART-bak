"""Unregistered GET /api/intercept/rules adapter. No mutations or activation.

Requires explicit global raw-policy disclosure authority. JWT alone is not that
permission. Unknown query/body rejected; source fields and disabled rules retained.
"""
from urllib.parse import urlsplit
from ..db.tool_intercept_read import ToolInterceptReader,RulesUnavailable
from .core import json_response,error_response

class InvalidRequest(ValueError):pass
class ToolInterceptReadService:
    def __init__(self,reader,*,authorize,current,lifecycle_guard):
        if type(reader) is not ToolInterceptReader or any(not callable(c) for c in (authorize,current,lifecycle_guard)):raise ValueError('Explicit raw rule read authority required')
        reader.validate();self.reader=reader;self.authorize=authorize;self.current=current;self.guard=lifecycle_guard
        self._pins=(reader,reader.executor,authorize,current,lifecycle_guard)
    def _check(self):
        r,e,a,c,g=self._pins
        if self.reader is not r or r.executor is not e or self.authorize is not a or self.current is not c or self.guard is not g:raise RulesUnavailable()
        r.validate()
    def handle(self,request):
        try:
            if request.method!='GET' or request.path!='/api/intercept/rules' or request.body or urlsplit(request.target).query:raise InvalidRequest()
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
                allow();rules=self._pins[0].read();allow()
                response=json_response(200,{'rules':rules});allow()
            allow();return response
        except InvalidRequest:return error_response(400,'Invalid intercept rules request')
        except PermissionError:return error_response(403,'Intercept rules read denied')
        except Exception:return error_response(500,'Intercept rules unavailable')
