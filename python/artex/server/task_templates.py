"""Four authenticated template routes, no task creation or execution.

Stricter JSON than upstream: unknown/duplicate fields, explicit null text, malformed
scope/query selectors rejected. Category FK errors are deterministic 400; backend
errors redacted. DELETE permanently removes only a preset, exactly as upstream.
Authorization is the existing Router administrator boundary, not a new user scope.
"""
import json,re
from urllib.parse import urlsplit
from .core import Router,json_response,error_response
from ..db.task_templates import TemplateInvalid,TemplateConflict,TemplateMissing,identifier
MAX_BODY=512<<10


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise TemplateInvalid('Duplicate JSON key')
        result[key]=value
    return result


class TaskTemplateService:
    def __init__(self,store=None):self.store=store
    def register(self,router:Router):
        for method,path,action in (
            ('GET','/api/task-templates','list'),('POST','/api/task-templates','create'),
            ('PATCH','/api/task-templates/{id}','patch'),('DELETE','/api/task-templates/{id}','delete'),
        ):
            router.add(method,path,lambda request,params,action=action:self._handle(action,request,params))
    def _handle(self,action,request,params):
        try:
            if urlsplit(request.target).query:raise TemplateInvalid('Query filters unsupported')
            if self.store is None:return error_response(503,'Task template store unavailable')
            if action=='list':return json_response(200,{'templates':[item.to_dict() for item in self.store.list()]})
            key=None
            if 'id' in params:
                if not re.fullmatch('[0-9]{1,19}',params['id']):raise TemplateInvalid('Invalid template ID')
                key=identifier(int(params['id']))
            if action=='delete':
                if not self.store.delete(key):raise TemplateMissing()
                return json_response(200,{'deleted':key})
            if len(request.body)>MAX_BODY:return error_response(413,'Request body too large')
            # Match the JSON wire contract rather than json.loads(bytes), which
            # auto-detects UTF-16/32 and silently consumes a leading UTF-8 BOM.
            try:
                data=json.loads(request.body.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(TemplateInvalid()))
            except RecursionError:
                raise TemplateInvalid('JSON nesting exceeds decoder limit') from None
            result=self.store.create(data) if action=='create' else self.store.patch(key,data)
            return json_response(201 if action=='create' else 200,result.to_dict())
        except (TemplateInvalid,ValueError,UnicodeError):return error_response(400,'Invalid task template request')
        except TemplateConflict:return error_response(409,'Template name already exists')
        except TemplateMissing:return error_response(404,'Task template not found')
        except Exception:return error_response(500,'Task template operation failed')
