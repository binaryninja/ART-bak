"""Standalone GET detail only; no registration, decisions or execution navigation."""
import re,json
from urllib.parse import urlsplit
from ..db.intercept_detail_read import InterceptDetailReader,DetailUnavailable
from .core import json_response,error_response

class InvalidDetailRequest(ValueError):pass
class InterceptDetailReadService:
    def __init__(self,reader,*,authorize,raw_authorize,current,lifecycle_guard):
        if type(reader) is not InterceptDetailReader or any(not callable(v) for v in (authorize,raw_authorize,current,lifecycle_guard)):raise ValueError('Explicit record and raw diagnostic grants required')
        reader.validate();self.reader=reader;self.authorize=authorize;self.raw_authorize=raw_authorize;self.current=current;self.guard=lifecycle_guard
        self._pins=(reader,reader.executor,reader.scope,authorize,raw_authorize,current,lifecycle_guard)
    def _check(self):
        r,e,s,a,raw,c,g=self._pins
        if self.reader is not r or r.executor is not e or r.scope is not s or self.authorize is not a or self.raw_authorize is not raw or self.current is not c or self.guard is not g:raise PermissionError()
        r.validate()
    def handle(self,request):
        try:
            match=re.fullmatch(r'/api/intercept/history/([1-9][0-9]{0,18})',request.path)
            if request.method!='GET' or not match or request.body or urlsplit(request.target).query:raise InvalidDetailRequest()
            id=int(match[1])
            if id>=2**63:raise InvalidDetailRequest()
            r,e,s,a,raw,c,g=self._pins
            def check_current():
                self._check()
                if c(s) is not True:raise PermissionError()
                self._check()
            def allow():
                check_current()
                if a(request,s,id) is not True:raise PermissionError()
                check_current()
                if raw(request,s,id) is not True:raise PermissionError()
                check_current()
                if a(request,s,id) is not True:raise PermissionError()
                check_current()
            allow()
            with g(s):
                allow();detail=r.read(id);allow()
                response=error_response(404,'not found') if detail is None else json_response(200,detail)
                if len(response.body)>r.max_bytes*6+4096:raise DetailUnavailable()
                allow()
            allow();return response
        except InvalidDetailRequest:return error_response(400,'bad approval id')
        except PermissionError:return error_response(403,'Intercept detail read denied')
        except Exception:return error_response(500,'Intercept detail unavailable')
