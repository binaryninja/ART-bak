"""Optional source token summary GETs; fixed scope, no billing estimates.

Task service requires explicit ?task=<its exact numeric ID>, never ambient active
selection. Global service authorizes entire workspace for daily/conversations.
No date/timezone extensions beyond source ?days; missing/error data is not zero.
No default registration, provider calls, profile credential or price reads.
"""
from urllib.parse import parse_qsl,urlsplit
import re
from ..db.token_usage import TokenUsageStore,UsageDenied,UsageUnavailable
from .core import json_response,error_response

class TokenUsageService:
    def __init__(self,store,*,authorize,max_response_bytes=2097152):
        if type(store) is not TokenUsageStore or not callable(authorize):raise ValueError('Actual scoped usage store and request authority required')
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=16777216:raise ValueError('Bounded response required')
        self.store=store;self._store=store;self._executor=store.executor;self._scope=store.scope;self._guard=store.guard
        self.authorize=authorize;self.max_bytes=max_response_bytes
    def _check(self):
        s=self.store
        if s is not self._store or s.executor is not self._executor or s._executor is not self._executor or s.scope is not self._scope or s._scope is not self._scope or s.guard is not self._guard:raise UsageDenied()
        s._check()
        if self.store is not s or s.executor is not self._executor or s.scope is not self._scope or s.guard is not self._guard:raise UsageDenied()
    def register(self,router):
        for path,op in (('/api/tokens/daily','daily'),('/api/tokens/conversations','conversations'),('/api/exploration/tokens','exploration')):
            router.add('GET',path,lambda req,params,a=op:self.handle(a,req,params))
    def handle(self,op,request,params=None):
        policies=(self.authorize,getattr(self.store,'authorize',None),getattr(self.store,'current',None),)
        def check():
            if any(a is not b for a,b in zip((self.authorize,getattr(self.store,'authorize',None),getattr(self.store,'current',None),),policies)):raise UsageDenied()
            self._check()
            if any(a is not b for a,b in zip((self.authorize,getattr(self.store,'authorize',None),getattr(self.store,'current',None),),policies)):raise UsageDenied()

        try:
            if request.body:raise ValueError()
            query=urlsplit(request.target).query
            if len(query)>128:raise ValueError()
            pairs=parse_qsl(query,keep_blank_values=True,max_num_fields=2)
            if len({k for k,v in pairs})!=len(pairs):raise ValueError()
            q=dict(pairs);days=30
            if op=='daily':
                if set(q)-{'days'}:raise ValueError()
                if 'days' in q:
                    if not re.fullmatch('-?[0-9]{1,5}',q['days']):raise ValueError()
                    days=int(q['days'])
            elif op=='conversations':
                if q:raise ValueError()
            elif op=='exploration':
                if set(q)!={'task'} or not re.fullmatch('[1-9][0-9]{0,18}',q['task']):raise ValueError()
                if int(q['task'])!=self._scope.task_id:raise UsageDenied()
            else:raise ValueError()
            with self._guard(self._scope):
                check()
                if self.authorize(request,self._scope,op) is not True:raise UsageDenied()
                check()
                result=self.store.daily(days) if op=='daily' else self.store.conversations() if op=='conversations' else self.store.exploration()
                check()
                if self.authorize(request,self._scope,op) is not True:raise UsageDenied()
                check();self.store.validate_read(op)
                check();response=json_response(200,result)
                if len(response.body)>self.max_bytes:raise UsageUnavailable()
                return response
        except UsageDenied:return error_response(403,'Token scope not authorized')
        except (ValueError,UnicodeError):return error_response(400,'Invalid token query')
        except Exception:return error_response(503,'Token accounting unavailable')
