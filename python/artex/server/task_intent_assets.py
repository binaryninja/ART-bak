"""Fixed-scope optional intent asset metadata, no target/tool execution."""
from urllib.parse import urlsplit
from ..db.task_intent_assets import TaskIntentAssetsStore
from ..db.task_coverage import CoverageDenied,CoverageUnavailable
from .core import json_response,error_response
from .task_goals import _id
class TaskIntentAssetsService:
    def __init__(self,store,*,authorize):
        if type(store) is not TaskIntentAssetsStore or not callable(authorize):raise ValueError('Actual store and explicit raw metadata grant required')
        self.store=store;self._store=store;self._executor=store.executor;self._scope=store.scope;self._guard=store.guard;self.authorize=authorize
    def _check(self):
        if self.store is not self._store or self.store.executor is not self._executor or self.store.scope is not self._scope or self.store.guard is not self._guard:raise CoverageDenied()
        self.store._check()
    def register(self,router):
        router.add('GET','/api/tasks/{id}/intent-assets',self.handle)
    def handle(self,request,params):
        policy=self.authorize
        def check():
            if self.authorize is not policy:raise CoverageDenied()
            self._check()
            if self.authorize is not policy:raise CoverageDenied()
        def disclosure():
            check()
            if policy(request,self._scope) is not True:raise CoverageDenied()
            check();return True
        try:
            if request.body or urlsplit(request.target).query:raise ValueError()
            if _id(params['id'])!=self._scope.task_id:raise CoverageDenied()
            disclosure()
            assets,final_disclosure=self.store.list(disclosure=disclosure,with_disclosure_receipt=True);check()
            response=json_response(200,{'assets':assets})
            if len(response.body)>self.store.coverage.max_bytes:raise CoverageUnavailable()
            final_disclosure();check();return response
        except CoverageDenied:return error_response(403,'Intent asset inspection not authorized')
        except (ValueError,KeyError):return error_response(400,'Invalid task selector')
        except Exception:return error_response(503,'Intent asset inspection unavailable')
