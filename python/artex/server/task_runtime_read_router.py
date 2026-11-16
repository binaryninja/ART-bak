"""Dynamic explicit task read selection under one existing JWT application.

Selector permission precedes all task SQL. Each request constructs independent
fixed-scope services; a stream retains that scope and its request grants forever.
No active/global fallback, task cache, inferred execution authority or mutations.
Read-policy revision is host supplied and separate from model/runtime revisions.
"""
from copy import deepcopy
import re
from urllib.parse import parse_qs,urlsplit
from .core import Router,error_response
from .wsgi import WSGIApplication
from .task_runtime_reads import TaskReadScope
from .profiles import ProfileService
from ..db.profiles import ProfileStore
from ..db.tasks import TaskStore
from ..db.core import Query

class ReadDenied(PermissionError):pass
class ReadMissing(LookupError):pass

class TaskRuntimeReadRouter:
    def __init__(self,tasks,builder,*,revision,authorize_selector,current,lifecycle_guard,authorize_profiles=None):
        from .task_read_builder import TaskReadBuilder
        if type(tasks) is not TaskStore or type(builder) is not TaskReadBuilder or builder.executor is not tasks.executor:
            raise ValueError('Actual same-executor task store and read builder required')
        if type(revision) is not str or not 1<=len(revision.encode())<=256 or any(not callable(f) for f in (authorize_selector,current,lifecycle_guard)) or (authorize_profiles is not None and not callable(authorize_profiles)):
            raise ValueError('Explicit selector and read lifetime authority required')
        self.tasks,self.builder=tasks,builder;self.revision=revision
        self.authorize_selector,self.current,self.guard,self.authorize_profiles=authorize_selector,current,lifecycle_guard,authorize_profiles
        self._pins=(tasks,builder,tasks.executor,revision,authorize_selector,current,lifecycle_guard,authorize_profiles)
    def validate_bindings(self):
        t,b,e,r,a,c,g,p=self._pins
        if self.tasks is not t or self.builder is not b or t.executor is not e or b.executor is not e or self.revision!=r or self.authorize_selector is not a or self.current is not c or self.guard is not g or self.authorize_profiles is not p:raise ReadDenied()
        b.validate_bindings();return True
    @staticmethod
    def _operation(path):
        match=re.fullmatch(r'/api/tasks/([^/]+)/(coverage|coverage-graph|asset-refs)',path)
        if match:return {'coverage':'coverage','coverage-graph':'coverage_graph','asset-refs':'coverage_refs'}[match[2]],{'id':match[1]}
        if re.fullmatch(r'/api/tasks/[^/]+/intent-assets',path):return 'intent_assets',{'id':path.split('/')[-2]}
        if re.fullmatch(r'/api/tasks/[^/]+/goals',path):return 'goals',{'id':path.split('/')[-2]}
        if re.fullmatch(r'/api/tasks/[^/]+/constraints',path):return 'constraints',{'id':path.split('/')[-2]}
        if re.fullmatch(r'/api/tasks/[^/]+/scope',path):return 'scope',{'id':path.split('/')[-2]}
        if path=='/api/llm/records/by-model':return 'model_usage',{}
        if path=='/api/exploration/tokens':return 'token_usage',{}
        if path=='/api/exploration/main-sessions':return 'main_sessions',{}
        if path=='/api/llm/profiles':return 'profiles',{}
        if re.fullmatch(r'/api/tasks/[^/]+',path):return 'task',{'id':path.rsplit('/',1)[1]}
        for path_,op in (('/api/stats','stats'),('/api/exploration/intents','intents'),('/api/exploration/findings','findings'),('/api/exploration/activity','list'),('/api/exploration/activity/history','history'),('/api/exploration/activity/stream','stream')):
            if path==path_:return op,{}
        if re.fullmatch(r'/api/exploration/activity/[^/]+',path):return 'detail',{'seq':path.rsplit('/',1)[1]}
        return None,{}
    def bind(self,application):
        if type(application) is not WSGIApplication or type(application.router) is not Router:raise ValueError('One original authenticated application required')
        self.validate_bindings();base=application.router;auth=base.authorize;owner=self
        protected=Router(auth)
        for path in ('/api/tasks/{id}/constraints','/api/tasks/{id}/scope','/api/llm/records/by-model','/api/exploration/tokens','/api/tasks/{id}/intent-assets','/api/tasks/{id}/coverage','/api/tasks/{id}/coverage-graph','/api/tasks/{id}/asset-refs','/api/tasks/{id}/goals','/api/exploration/main-sessions','/api/tasks/{id}','/api/stats','/api/exploration/intents','/api/exploration/findings','/api/llm/profiles','/api/exploration/activity','/api/exploration/activity/history','/api/exploration/activity/stream','/api/exploration/activity/{seq}'):
            protected.add('GET',path,lambda req,params:owner._read(req,base,auth))
        class Overlay:
            def dispatch(self,request):
                if base.authorize is not auth:return error_response(503,'Read authentication changed')
                op,_=owner._operation(request.path)
                if op is not None and request.method.upper() in ('GET','HEAD'):return protected.dispatch(request)
                return base.dispatch(request)
        return WSGIApplication(Overlay(),max_body_bytes=application.max_body_bytes)
    def _allow(self,request,task_id):
        self.validate_bindings()
        if self._pins[4](request,task_id) is not True:raise ReadDenied()
        self.validate_bindings()
    def _read(self,request,base,auth):
        def auth_binding():
            if base.authorize is not auth:raise ReadDenied()
            self.validate_bindings()
        def selector(task_id):
            auth_binding();self._allow(request,task_id);auth_binding()
        try:
            auth_binding()
            request=deepcopy(request);auth_binding()
            if request.body:raise ValueError()
            op,params=self._operation(request.path)
            query=urlsplit(request.target).query
            if len(query)>20000:raise ValueError()
            q=parse_qs(query,keep_blank_values=True,strict_parsing=True,max_num_fields=10,errors='strict')
            if any(len(v)!=1 for v in q.values()):raise ValueError()
            if op=='profiles':
                if q:raise ValueError()
                def allowed():
                    auth_binding()
                    if self._pins[7] is None or self._pins[7](request) is not True:raise ReadDenied()
                    auth_binding()
                allowed();response=ProfileService(ProfileStore(self._pins[2]))._handle('list',request);allowed();return response
            raw=params['id'] if 'id' in params else q.get('task',[''])[0]
            if not re.fullmatch('[1-9][0-9]{0,18}',raw) or int(raw)>=2**63:raise ValueError()
            task_id=int(raw)
            if op in ('task','goals','coverage','coverage_graph','intent_assets','scope','constraints') and q:raise ValueError()
            selector(task_id)
            task=self._pins[0].get(task_id)
            selector(task_id)
            if task is None:raise ReadMissing()
            if task.id!=task_id or type(task.exploration_id) is not int or task.exploration_id<=0:raise ReadMissing()
            scope=TaskReadScope(task_id,task.exploration_id,self._pins[3])
            def current(candidate):
                auth_binding()
                if (candidate.task_id,candidate.exploration_id,candidate.revision)!=(scope.task_id,scope.exploration_id,scope.revision):raise ReadDenied()
                selector(task_id)
                if self._pins[5](scope) is not True:raise ReadDenied()
                auth_binding()
                rows=self._pins[2].fetch_all(Query('''SELECT exploration_id FROM tasks WHERE id=$1 AND exploration_id=$2
AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1)''',(task_id,scope.exploration_id)))
                selector(task_id)
                if rows not in ([(scope.exploration_id,)],[[scope.exploration_id]]):raise ReadMissing()
                if self._pins[5](scope) is not True:raise ReadDenied()
                auth_binding();return True
            def guard(candidate):
                if (candidate.task_id,candidate.exploration_id,candidate.revision)!=(scope.task_id,scope.exploration_id,scope.revision):raise ReadDenied()
                self.validate_bindings();return self._pins[6](scope)
            with guard(scope):current(scope)
            services=self._pins[1].build(scope,current=current,lifecycle_guard=guard)
            self.validate_bindings()
            if op in ('coverage','coverage_graph','coverage_refs'):
                if services.coverage is None:return error_response(503,'Coverage read capability unavailable')
                response=services.coverage.handle({'coverage':'coverage','coverage_graph':'graph','coverage_refs':'refs'}[op],request)
            elif op in ('intent_assets','token_usage','model_usage','scope','constraints'):
                if op=='constraints' and services.constraints is None:return error_response(503,'Constraint read capability unavailable')
                if op=='scope' and services.scope is None:return error_response(503,'Scope read capability unavailable')
                if op=='intent_assets' and services.intent_assets is None:return error_response(503,'Intent-asset read capability unavailable')
                if op=='token_usage' and services.token_usage is None:return error_response(503,'Token read capability unavailable')
                if op=='model_usage' and services.model_usage is None:return error_response(503,'By-model read capability unavailable')
                def transport_disclosure():
                    auth_binding()
                    if auth(request) is not True:raise ReadDenied()
                    selector(task_id)
                    current(scope)
                    auth_binding()
                    if auth(request) is not True:raise ReadDenied()
                def transport_authentication():
                    # Authority-only selector sweep after every current/source
                    # observer; do not call current(scope) again here.
                    selector(task_id)
                    auth_binding()
                    if auth(request) is not True:raise ReadDenied()
                    auth_binding()
                if op=='intent_assets':
                    response=services.intent_assets.handle(request,{'id':str(task_id)},transport_disclosure=transport_disclosure,transport_authentication=transport_authentication)
                elif op=='constraints':
                    response=services.constraints.handle(request,transport_disclosure=transport_disclosure,transport_authentication=transport_authentication)
                elif op=='scope':
                    response=services.scope.handle(request,transport_disclosure=transport_disclosure,transport_authentication=transport_authentication)
                elif op=='model_usage':
                    response=services.model_usage.handle(request,transport_disclosure=transport_disclosure,transport_authentication=transport_authentication)
                else:
                    response=services.token_usage.handle('exploration',request,transport_disclosure=transport_disclosure,transport_authentication=transport_authentication)
            elif op=='goals':
                if services.goals is None:return error_response(503,'Goal read capability unavailable')
                response=services.goals.handle(request)
            elif op=='main_sessions':
                if services.main_sessions is None:return error_response(503,'Main-session read capability unavailable')
                response=services.main_sessions.handle(request)
            elif op in ('list','history','stream','detail'):response=services.activity.handle(op,request,params)
            else:response=services.reads._read(request,op,base)
            auth_binding()
            return response
        except ReadDenied:return error_response(403,'Task selector access denied')
        except ReadMissing:return error_response(404,'Local task read scope unavailable')
        except (ValueError,UnicodeError):return error_response(400,'Explicit task selector required')
        except Exception:return error_response(503,'Task read capability unavailable')
