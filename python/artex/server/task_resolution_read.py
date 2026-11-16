"""Opt-in static LLM resolution metadata; no provider, fallback invention or writes."""
from urllib.parse import urlsplit
from .core import error_response
from .disclosure import DisclosureResponse
from .task_profiles import TaskProfileService,TaskProfileBinding,AccessDenied
from ..db.tasks import TaskStore
from ..db.profiles import ProfileStore
from ..db.agents import AgentStore

class TaskResolutionRead:
    def __init__(self,executor,scope,*,authorize,request_authorize,profile_authorize,environment_authorize,configuration_valid,environment,current,lifecycle_guard,max_bytes=1048576):
        from .task_runtime_reads import TaskReadScope
        if type(scope) is not TaskReadScope:raise ValueError('Exact resolved scope required')
        callbacks=(authorize,request_authorize,profile_authorize,environment_authorize,configuration_valid,environment,current,lifecycle_guard)
        if any(not callable(f) for f in callbacks):raise ValueError('Explicit independent configuration authority required')
        if type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Bounded response required')
        self._pins=(executor,scope,*callbacks,max_bytes);self._original=self._pins
        self._identity=(scope.task_id,scope.exploration_id,scope.revision)
    def handle(self,request,*,transport_disclosure,transport_authentication):
        e,s,source,http,profile,env_grant,valid,environment,current,guard,maximum=self._original
        inspected=set();environment_seen=[False]
        def pure():
            if self._pins is not self._original or (s.task_id,s.exploration_id,s.revision)!=self._identity:raise AccessDenied()
        def check():
            pure()
            if current(s) is not True:raise AccessDenied()
            pure();return True
        def authority():
            pure()
            if http(request,s) is not True:raise AccessDenied()
            pure()
            if source(s) is not True:raise AccessDenied()
            pure()
            for identifier in sorted(inspected):
                if profile(request,s,identifier) is not True:raise AccessDenied()
                pure()
            if environment_seen[0]:
                if env_grant(request,s) is not True:raise AccessDenied()
                pure()
            return True
        def profile_gate(req,binding,identifier):
            authority()
            if profile(request,s,identifier) is not True:raise AccessDenied()
            pure();inspected.add(identifier);return True
        def resolve_environment(req,binding):
            authority()
            if env_grant(request,s) is not True:raise AccessDenied()
            pure();environment_seen[0]=True
            result=environment(request,s);pure();return result
        def validate(configuration):
            authority();result=valid(configuration);pure();return result
        def receipt():
            authority();transport_disclosure()
            with guard(s):check()
            check();authority();transport_authentication();pure()
        try:
            if not callable(transport_disclosure) or not callable(transport_authentication):raise ValueError()
            if request.method.upper() not in ('GET','HEAD') or request.body or urlsplit(request.target).query or request.path!=f'/api/tasks/{s.task_id}/llm/resolution':raise ValueError()
            receipt()
            service=TaskProfileService(TaskStore(e),ProfileStore(e),AgentStore(e),
                resolve=lambda req,task:TaskProfileBinding(*self._identity) if task==s.task_id else None,
                authorize=lambda *a:authority(),profile_authorize=profile_gate,current=lambda b:check(),
                lifecycle_guard=lambda b:guard(s),configuration_valid=validate,environment=resolve_environment)
            response=service.handle('resolution',request,{'id':str(s.task_id)})
            receipt()
            if len(response.body)>maximum:raise RuntimeError()
            if response.status!=200:return response
            return DisclosureResponse(response.status,response.body,response.headers,receipt)
        except AccessDenied:return error_response(403,'Task resolution read denied')
        except ValueError:return error_response(400,'Invalid task resolution request')
        except Exception:return error_response(503,'Task resolution unavailable')
