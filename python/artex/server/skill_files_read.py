"""Standalone GET skill file names; filesystem errors are sanitized. No registration or implicit global permission."""
import re
from urllib.parse import urlsplit,parse_qsl,unquote
from ..skills.file_listing import SkillFileListingReader,SkillListingUnavailable,SkillListingConflict,SkillListingLimit
from ..skills import valid_skill_name
from .core import Request,json_response,error_response

class InvalidRequest(ValueError):pass

def parse_request(request):
    if request.method not in ('GET','HEAD') or request.body or len(request.target)>2048:raise InvalidRequest()
    try:
        parts=urlsplit(request.target)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:raise ValueError()
        match=re.fullmatch(r'/api/skills/([^/]+)/files',parts.path)
        if not match:raise ValueError()
        name=unquote(match[1],errors='strict')
        if not valid_skill_name(name):raise ValueError()
        return name
    except (ValueError,UnicodeError):raise InvalidRequest() from None

class SkillFilesReadService:
    def __init__(self,reader,*,authorize):
        if type(reader) is not SkillFileListingReader or not callable(authorize):raise ValueError('Exact reader and request authority required')
        reader.validate();self.reader=reader;self.authorize=authorize;self._pins=(reader,authorize)
    def _check(self):
        r,a=self._pins
        if self.reader is not r or self.authorize is not a:raise SkillListingUnavailable()
        r.validate()
    def handle(self,request):
        try:
            if type(request) is not Request or type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes:raise InvalidRequest()
            headers=request.headers
            if type(headers) is not dict or any(type(k) is not str or type(v) is not str for k,v in headers.items()):raise InvalidRequest()
            request_identity=(request.method,request.target,request.body,tuple(headers.items()))
            selected=parse_request(request);identity=selected
            def pins():
                self._check()
                if request.headers is not headers or type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes or any(type(k) is not str or type(v) is not str for k,v in headers.items()) or (request.method,request.target,request.body,tuple(headers.items()))!=request_identity:raise SkillListingUnavailable()
                if type(selected) is not str or selected!=identity:raise SkillListingUnavailable()
            def allow():
                pins()
                if self._pins[1](request,self._pins[0].scope,selected) is not True:raise PermissionError()
                pins()
            allow();value=self._pins[0].read(selected,disclosure=allow)
            response=json_response(200,value)
            self._pins[0].validate_disclosure(selected,disclosure=allow)
            pins();return response
        except InvalidRequest:return error_response(400,'Invalid skill file listing request')
        except FileNotFoundError:return error_response(404,'Skill not found')
        except SkillListingLimit:return error_response(413,'Skill file listing limit exceeded')
        except SkillListingConflict:return error_response(409,'Skill directory changed')
        except PermissionError:return error_response(403,'Skill file listing denied')
        except (SkillListingUnavailable,OSError):return error_response(503,'Skill file listing unavailable')
        except Exception:return error_response(500,'Skill file listing unavailable')
