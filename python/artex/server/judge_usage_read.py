"""Unregistered global GET /api/intercept/judge/usage, separate read authority."""
import re
from urllib.parse import urlsplit,parse_qsl
from ..db.judge_usage_read import JudgeUsageReader,UsageUnavailable,normalized_days
from .core import json_response,error_response

class InvalidRequest(ValueError):pass
class JudgeUsageReadService:
    def __init__(self,reader,*,authorize,current,lifecycle_guard):
        if type(reader) is not JudgeUsageReader or any(not callable(c) for c in (authorize,current,lifecycle_guard)):raise ValueError('Explicit global metering authority required')
        reader.validate();self.reader=reader;self.authorize=authorize;self.current=current;self.guard=lifecycle_guard
        self._pins=(reader,reader.executor,authorize,current,lifecycle_guard)
    def _check(self):
        r,e,a,c,g=self._pins
        if self.reader is not r or r.executor is not e or self.authorize is not a or self.current is not c or self.guard is not g:raise UsageUnavailable()
        r.validate()
    def handle(self,request):
        try:
            if request.method!='GET' or request.path!='/api/intercept/judge/usage' or request.body:raise InvalidRequest()
            query=urlsplit(request.target).query
            if len(query)>256:raise InvalidRequest()
            try:pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=2)
            except ValueError:raise InvalidRequest() from None
            if len(pairs)>1 or any(k!='days' for k,v in pairs):raise InvalidRequest()
            raw=pairs[0][1] if pairs else ''
            # Go strconv.Atoi rejects whitespace/non-ASCII; invalid/overflow falls back30.
            days=int(raw) if re.fullmatch(r'[+-]?[0-9]{1,256}',raw) else 30
            if not -2**63<=days<2**63:days=30
            try:days=normalized_days(days)
            except ValueError:raise InvalidRequest() from None
            def allow():
                self._check()
                if self._pins[3]() is not True:raise PermissionError()
                self._check()
                if self._pins[2](request,days) is not True:raise PermissionError()
                self._check()
                if self._pins[3]() is not True:raise PermissionError()
                self._check()
            allow()
            with self._pins[4]():
                allow();value=self._pins[0].read(days);allow()
                response=json_response(200,value);allow()
            allow();return response
        except InvalidRequest:return error_response(400,'Invalid judge usage request')
        except PermissionError:return error_response(403,'Judge usage read denied')
        except Exception:return error_response(500,'Judge usage unavailable')
