"""Standalone GET skill usage. No registration or implicit global permission."""
import re
from urllib.parse import urlsplit,parse_qsl,unquote
from ..db.skill_usage_read import SkillUsageReader,UsageSelection,UsageUnavailable
from .core import Request,json_response,error_response

class InvalidRequest(ValueError):pass

def parse_request(request):
    if request.method!='GET' or request.body or len(request.target)>2048:raise InvalidRequest()
    match=re.fullmatch(r'/api/skills/([^/]+)/usage',request.path)
    if not match:raise InvalidRequest()
    try:
        name=unquote(match[1],errors='strict')
        pairs=parse_qsl(urlsplit(request.target).query,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=2)
        if len(pairs)>1 or any(k!='limit' for k,v in pairs):raise ValueError()
        raw=pairs[0][1] if pairs else ''
        limit=int(raw) if re.fullmatch(r'[+-]?[0-9]{1,256}',raw) else 50
        if not 1<=limit<=200:limit=50
        selection=UsageSelection(name,limit);selection.validate();return selection
    except (ValueError,UnicodeError):raise InvalidRequest() from None

class SkillUsageReadService:
    def __init__(self,reader,*,authorize):
        if type(reader) is not SkillUsageReader or not callable(authorize):raise ValueError('Exact reader and request authority required')
        reader.validate();self.reader=reader;self.authorize=authorize;self._pins=(reader,authorize)
    def _check(self):
        r,a=self._pins
        if self.reader is not r or self.authorize is not a:raise UsageUnavailable()
        r.validate()
    def handle(self,request):
        try:
            if type(request) is not Request or type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes:raise InvalidRequest()
            headers=request.headers
            if type(headers) is not dict or any(type(k) is not str or type(v) is not str for k,v in headers.items()):raise InvalidRequest()
            request_identity=(request.method,request.target,request.body,tuple(headers.items()))
            selected=parse_request(request);identity=(selected.name,selected.limit)
            def pins():
                self._check()
                if request.headers is not headers or type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes or any(type(k) is not str or type(v) is not str for k,v in headers.items()) or (request.method,request.target,request.body,tuple(headers.items()))!=request_identity:raise UsageUnavailable()
                selected.validate()
                if (selected.name,selected.limit)!=identity:raise UsageUnavailable()
            def allow():
                pins()
                if self._pins[1](request,self._pins[0].scope,selected) is not True:raise PermissionError()
                pins()
            allow();value=self._pins[0].read(selected,disclosure=allow)
            response=json_response(200,value)
            self._pins[0].validate_disclosure(selected,disclosure=allow)
            allow();pins();return response
        except InvalidRequest:return error_response(400,'Invalid skill usage request')
        except PermissionError:return error_response(403,'Skill usage denied')
        except Exception:return error_response(500,'Skill usage unavailable')
