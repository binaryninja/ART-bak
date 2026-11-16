"""Authorized inert catalog DTO; filesystem exceptions never disclose paths."""
import json
from .core import Request,json_response,error_response
from ..skills.catalog_read import SkillCatalogReader,SkillCatalogConflict,SkillCatalogLimit,SkillCatalogUnavailable
from ..skills.file_read import SkillFileConflict,SkillFileUnavailable
from ..tools.filesystem import FilesystemError

class InvalidCatalogRequest(ValueError):pass

def parse_request(request):
    if request.method!='GET' or request.target!='/api/skills' or request.body:raise InvalidCatalogRequest()

class SkillCatalogReadService:
    def __init__(self,reader,*,authorize):
        if type(reader) is not SkillCatalogReader or not callable(authorize):raise ValueError('Exact catalog reader and authority required')
        reader.validate();self.reader,self.authorize=reader,authorize;self._pins=(reader,authorize)
    def _check(self):
        if self.reader is not self._pins[0] or self.authorize is not self._pins[1]:raise PermissionError()
        self.reader.validate()
    def _prepare(self,request):
        try:
            self._check()
            if type(request) is not Request or type(request.headers) is not dict or any(type(k) is not str or type(v) is not str for k,v in request.headers.items()):raise InvalidCatalogRequest()
            parse_request(request);headers=request.headers
            identity=(request.method,request.target,request.body,tuple(headers.items()))
            def pure():
                self._check()
                if request.headers is not headers or identity!=(request.method,request.target,request.body,tuple(request.headers.items())):raise PermissionError()
            def allow():
                pure()
                if self._pins[1](request,self.reader.scope) is not True:raise PermissionError()
                pure()
            allow();snapshot=self.reader.read(disclosure=allow)
            response=json_response(200,json.loads(snapshot.payload))
            self.reader.validate_disclosure(snapshot,disclosure=allow);pure()
            return response,snapshot
        except InvalidCatalogRequest:response=error_response(400,'Invalid skill catalog request')
        except (SkillCatalogConflict,SkillFileConflict):response=error_response(409,'Skill catalog changed')
        except PermissionError:response=error_response(403,'Skill catalog denied')
        except (SkillCatalogLimit,FilesystemError):response=error_response(400,'Skill catalog unavailable or exceeds bounds')
        except (OSError,SkillCatalogUnavailable,SkillFileUnavailable):response=error_response(503,'Skill catalog unavailable')
        except Exception:response=error_response(500,'Skill catalog read failed')
        return response,None
    def handle(self,request):return self._prepare(request)[0]
