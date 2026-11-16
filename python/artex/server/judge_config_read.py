"""Unregistered GET /api/intercept/judge adapter. No mutations or activation.

Requires explicit global config and raw-prompt disclosure authority. JWT alone is not that
permission. Unknown query/body rejected; source configuration fields retained.
"""
from urllib.parse import urlsplit
from ..db.judge_config_read import JudgeConfigReader,JudgeConfigUnavailable
from .core import json_response,error_response

class InvalidRequest(ValueError):pass
class JudgeConfigReadService:
    def __init__(self,reader,*,authorize,raw_authorize,current,lifecycle_guard):
        if type(reader) is not JudgeConfigReader or any(not callable(c) for c in (authorize,raw_authorize,current,lifecycle_guard)):raise ValueError('Explicit config and raw-prompt read authority required')
        reader.validate();self.reader=reader;self.authorize=authorize;self.current=current;self.guard=lifecycle_guard;self.raw_authorize=raw_authorize
        self._pins=(reader,reader.executor,authorize,current,lifecycle_guard,raw_authorize)
    def _check(self):
        r,e,a,c,g,raw=self._pins
        if self.reader is not r or r.executor is not e or self.authorize is not a or self.current is not c or self.guard is not g or self.raw_authorize is not raw:raise JudgeConfigUnavailable()
        r.validate()
    def handle(self,request):
        try:
            if request.method!='GET' or request.path!='/api/intercept/judge' or request.body or urlsplit(request.target).query:raise InvalidRequest()
            def allow():
                self._check()
                if self._pins[3]() is not True:raise PermissionError()
                self._check()
                if self._pins[2](request) is not True:raise PermissionError()
                self._check()
                if self._pins[5](request) is not True:raise PermissionError()
                self._check()
                if self._pins[2](request) is not True:raise PermissionError()
                self._check()
                if self._pins[3]() is not True:raise PermissionError()
                self._check()
            allow()
            with self._pins[4]():
                allow();rules=self._pins[0].read();allow()
                response=json_response(200,rules);allow()
            allow();return response
        except InvalidRequest:return error_response(400,'Invalid judge configuration request')
        except PermissionError:return error_response(403,'Judge configuration read denied')
        except Exception:return error_response(500,'Judge configuration unavailable')
