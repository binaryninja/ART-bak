"""Standalone skill content HTTP contract; no routes or default authority."""
import re
from urllib.parse import urlsplit, unquote
from .core import Request, error_response, json_response
from ..skills.file_read import (SkillFileReader,SkillFileSelection,SkillFileUnavailable,
                               SkillFileConflict)
from ..tools.filesystem import FilesystemError


class InvalidContentRequest(ValueError): pass


def parse_request(request):
    if request.method!='GET' or request.body or len(request.target)>4096:
        raise InvalidContentRequest()
    try:
        parts=urlsplit(request.target)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:raise ValueError()
        match=re.fullmatch(r'/api/skills/([^/]+)/files/(.+)',parts.path)
        if not match:raise ValueError()
        selection=SkillFileSelection(unquote(match[1],errors='strict'),unquote(match[2],errors='strict'))
        selection.validate()
        return selection
    except (ValueError,UnicodeError):raise InvalidContentRequest() from None


class SkillContentReadService:
    def __init__(self,reader,*,authorize):
        if type(reader) is not SkillFileReader or not callable(authorize):
            raise ValueError('Exact content reader and request authority required')
        reader.validate();self.reader=reader;self.authorize=authorize
        self._pins=(reader,authorize)

    def _check(self):
        if self.reader is not self._pins[0] or self.authorize is not self._pins[1]:
            raise SkillFileUnavailable('Content service changed')
        self._pins[0].validate()

    def _prepare(self,request):
        try:
            if type(request) is not Request or type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes or type(request.headers) is not dict or any(type(k) is not str or type(v) is not str for k,v in request.headers.items()):
                raise InvalidContentRequest()
            headers=request.headers;request_key=(request.method,request.target,request.body,tuple(headers.items()))
            selection=parse_request(request);identity=(selection.name,selection.file)
            def pins():
                self._check()
                if request.headers is not headers or type(request.method) is not str or type(request.target) is not str or type(request.body) is not bytes or any(type(k) is not str or type(v) is not str for k,v in headers.items()) or (request.method,request.target,request.body,tuple(headers.items()))!=request_key or type(selection) is not SkillFileSelection or (selection.name,selection.file)!=identity:
                    raise SkillFileUnavailable('Content request changed')
                selection.validate()
            def allow():
                pins()
                if self._pins[1](request,self._pins[0].scope,selection) is not True:raise PermissionError()
                pins()
            allow()
            snapshot=self._pins[0].read(selection,disclosure=allow)
            response=json_response(200,{'content':snapshot.content,'file':snapshot.file})
            self._pins[0].validate_disclosure(selection,snapshot,disclosure=allow)
            pins()
            return response,selection,snapshot
        except InvalidContentRequest:response=error_response(400,'Invalid skill content request')
        except FileNotFoundError:response=error_response(404,'Skill file not found')
        except SkillFileConflict:response=error_response(409,'Skill file changed')
        except PermissionError:response=error_response(403,'Skill content denied')
        except FilesystemError:response=error_response(400,'Skill file unavailable or exceeds bounds')
        except (SkillFileUnavailable,OSError):response=error_response(503,'Skill content unavailable')
        except Exception:response=error_response(500,'Skill content unavailable')
        return response,None,None

    def handle(self,request):
        return self._prepare(request)[0]
