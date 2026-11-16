"""Explicit idle-only cache owner. Local generation is not a distributed lease.

Every interceptor using this adapter must be declared here. The host lifetime
reservation must prevent new execution/admission through the whole operation.
No callback is treated as cache invalidation: original Interceptor.invalidate is
actually called. Unknown persistence leaves this owner permanently quarantined.
"""
from contextlib import ExitStack
from threading import RLock
from . import Interceptor
from ..db.tool_intercept_config import *

class InterceptPolicyOwner:
    def __init__(self,store,interceptors,*,workspace,authorize,current,idle,lifecycle_guard):
        if type(workspace) is not str or not 1<=len(workspace.encode())<=256:raise ValueError("Explicit workspace required")
        self.workspace=workspace;self._workspace=workspace
        if type(store) is not ToolInterceptConfigStore or type(interceptors) is not tuple or not 1<=len(interceptors)<=64 or len({id(x) for x in interceptors})!=len(interceptors):raise ValueError('Exact bounded owners required')
        if any(type(x) is not Interceptor or x.store is not store for x in interceptors):raise ValueError('Exact interceptor adapter required')
        if any(not callable(x) for x in (authorize,current,idle,lifecycle_guard)):raise ValueError('Explicit policies required')
        if getattr(store,'_policy_owner',None) is not None:raise ValueError('Adapter already owned')
        store.validate();self.store=store;self.interceptors=interceptors
        self.authorize=authorize;self.current=current;self.idle=idle;self.guard=lifecycle_guard
        self._pins=(store,interceptors,authorize,current,idle,lifecycle_guard)
        self._locks=tuple(x._lock for x in interceptors);self._lock=RLock();self._active=False;self.unresolved=False;self.generation=0
        self._transaction=store._executor.transaction
        store._policy_owner=self
    def validate(self):
        s,ics,a,c,i,g=self._pins
        if self.unresolved:raise PolicyUncertain('Policy reconciliation required')
        if self.workspace!=self._workspace or s._executor.transaction!=self._transaction:raise PolicyDenied("Workspace resource changed")
        if self.store is not s or self.interceptors is not ics or self.authorize is not a or self.current is not c or self.idle is not i or self.guard is not g or s._policy_owner is not self:raise PolicyDenied('Policy binding changed')
        s.validate()
        for ic,lock in zip(ics,self._locks):
            if ic.store is not s or ic._lock is not lock or getattr(ic.invalidate,'__func__',None) is not Interceptor.invalidate:raise PolicyDenied('Interceptor binding changed')
    def _allow(self,m,request_authorize,expected=None):
        if expected is None:expected=mutation_identity(m)
        def pure():
            self.validate()
            if mutation_identity(m)!=expected:raise PolicyDenied("Approved mutation changed")
            if any(x._pending for x in self._pins[1]):raise PolicyDenied("Pending approvals retained")
        pure()
        for fn,args in ((self._pins[3],()),(self._pins[4],(self._pins[1],)),(self._pins[2],(m,))):
            if fn(*args) is not True:raise PolicyDenied('Policy mutation denied')
            pure()
        # Original request/JWT/global grant is the last callback before effect.
        if request_authorize(m) is not True:raise PolicyDenied('Request authority revoked')
        pure()
    def mutate(self,m,*,request_authorize):
        if type(m) is not PolicyMutation or not callable(request_authorize):raise ValueError('Exact mutation required')
        expected=mutation_identity(m);attempted=False
        def attempt():
            nonlocal attempted
            attempted=True
        with self._lock:
            self.validate()
            if self._active:raise PolicyDenied('Reentrant mutation denied')
            self._active=True
            try:
                with self._pins[5](),ExitStack() as stack:
                    for lock in self._locks:stack.enter_context(lock)
                    self._allow(m,request_authorize,expected)
                    with self._pins[0]._executor.transaction() as tx:
                        result=self._pins[0]._apply(tx,m,lambda:self._allow(m,request_authorize,expected),attempt)
                        self._allow(m,request_authorize,expected)
                    # Cache reset is code-owned, performed before any host callback.
                    for ic in self._pins[1]:Interceptor.invalidate(ic)
                    self._allow(m,request_authorize,expected)
                self._allow(m,request_authorize,expected)
                self.generation+=1
                return result
            except BaseException:
                if attempted:
                    self.unresolved=True
                    self._pins[0]._unresolved=True
                    for ic in self._pins[1]:
                        with self._locks[self._pins[1].index(ic)]:ic._rules=ic._tools=None
                    raise PolicyUncertain('Policy persistence requires reconciliation; do not retry') from None
                raise
            finally:self._active=False
