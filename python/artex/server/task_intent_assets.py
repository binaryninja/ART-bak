"""Fixed-scope optional intent asset metadata, no target/tool execution."""
from urllib.parse import urlsplit
from ..db.task_intent_assets import TaskIntentAssetsStore
from ..db.task_coverage import CoverageDenied,CoverageUnavailable
from .core import json_response,error_response
from .disclosure import DisclosureResponse
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
    def handle(self,request,params,*,transport_disclosure=None,transport_authentication=None):
        if (transport_disclosure is None)!=(transport_authentication is None) or any(f is not None and not callable(f) for f in (transport_disclosure,transport_authentication)):raise ValueError('Paired transport receipts required')
        policy=self.authorize
        def check():
            if self.authorize is not policy:raise CoverageDenied()
            self._check()
            if self.authorize is not policy:raise CoverageDenied()
        def pure():
            if (self.authorize is not policy or self.store is not self._store
                    or self.store.executor is not self._executor or self.store.scope is not self._scope
                    or self.store.guard is not self._guard):raise CoverageDenied()
        def request_authority():
            pure()
            result=policy(request,self._scope)
            pure();return result is True
        def disclosure():
            check()
            if policy(request,self._scope) is not True:raise CoverageDenied()
            check();return True
        try:
            if request.body or urlsplit(request.target).query:raise ValueError()
            if _id(params['id'])!=self._scope.task_id:raise CoverageDenied()
            disclosure()
            assets,final_disclosure=self.store.list(disclosure=disclosure,with_disclosure_receipt=True,final_request_authorize=request_authority);pure()
            response=json_response(200,{'assets':assets})
            if len(response.body)>self.store.coverage.max_bytes:raise CoverageUnavailable()
            final_disclosure();pure()
            if transport_disclosure is None:return response
            def receipt():
                transport_disclosure()
                final_disclosure();pure()
                # JWT-only final observer: no selector/current callbacks after
                # the source receipt. Finite ordering, not callback fixed point.
                transport_authentication()
            receipt()
            return DisclosureResponse(response.status,response.body,response.headers,receipt)
        except CoverageDenied:return error_response(403,'Intent asset inspection not authorized')
        except (ValueError,KeyError):return error_response(400,'Invalid task selector')
        except Exception:return error_response(503,'Intent asset inspection unavailable')
