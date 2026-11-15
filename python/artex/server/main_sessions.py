"""Opt-in GET main-sessions with explicit exact ?task; no ambient selection."""
from urllib.parse import urlsplit,parse_qsl
from .core import json_response,error_response
from .task_goals import _id
from ..db.main_sessions import MainSessionsStore,SessionDenied,SessionUnavailable
class MainSessionsService:
    def __init__(self,store,*,authorize,max_response_bytes=1048576):
        if type(store) is not MainSessionsStore or not callable(authorize):raise ValueError('Actual store and request grant required')
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=16777216:raise ValueError('Invalid response bound')
        self.store=store;self._store=store;self._executor=store.executor;self._scope=store.scope;self._guard=store.guard
        self.authorize=authorize;self.max_bytes=max_response_bytes
    def register(self,router):router.add('GET','/api/exploration/main-sessions',lambda req,params:self.handle(req))
    def handle(self,request):
        policy=self.authorize;s=self._store
        def check():
            if self.authorize is not policy or self.store is not s or s.executor is not self._executor or s.scope is not self._scope or s.guard is not self._guard:raise SessionDenied()
        def allow():
            check()
            if policy(request,self._scope) is not True:raise SessionDenied()
            check();return True
        def project(result):
            response=json_response(200,result)
            if len(response.body)>self.max_bytes:raise SessionUnavailable()
            allow();return response
        try:
            if request.body:raise ValueError()
            query=urlsplit(request.target).query
            if len(query)>128:raise ValueError()
            pairs=parse_qsl(query,keep_blank_values=True,max_num_fields=2)
            if len(pairs)!=1 or pairs[0][0]!='task':raise ValueError()
            if _id(pairs[0][1])!=self._scope.task_id:raise SessionDenied()
            allow();response=s.read(project=project,disclosure=allow);check();return response
        except SessionDenied:return error_response(403,'Session inspection not authorized')
        except (ValueError,UnicodeError):return error_response(400,'Invalid task selector')
        except Exception:return error_response(503,'Session inspection unavailable')
