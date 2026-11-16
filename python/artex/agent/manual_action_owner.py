"""Explicit exact-instance lifetime tracking; no execution or shutdown authority.

A sealed owner refuses new direct observer/finalizer calls. Active invocation
receipts live through outermost finally, including after manager retirement.
Registration never runs policy/SQL/providers; cancellation runs outside locks.
"""
from dataclasses import dataclass
from threading import Condition,RLock,get_ident
import math
from .host import AgentHost

class ManualActionUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class ManualActionSnapshot:
    active:int
    unresolved:int
    sealed:bool
    @property
    def drained(self):return self.active==0 and self.unresolved==0

@dataclass(frozen=True,eq=False)
class _Invocation:
    component:object
    operation:str
    thread:int

class ManualActionOwner:
    def __init__(self,host,*,max_components=1024):
        if type(host) is not AgentHost or type(max_components) is not int or not 1<=max_components<=10000:
            raise ValueError('Actual host and bounded manual inventory required')
        self.host=host;self.max_components=max_components
        self._pins=(host,host.manager,host.runner,host.manager.repository,host.manager.repository.executor,max_components)
        self._condition=Condition(RLock());self._components={};self._active={};self._unresolved=set();self._sealed=False;self._application=None
    def validate_bindings(self):
        h,m,r,repo,e,bound=self._pins
        if self.host is not h or self.max_components!=bound or h.manager is not m or h.runner is not r or m.runner is not r or m.repository is not repo or repo.executor is not e:
            raise ManualActionUnavailable('Manual action resources changed')
        return True
    def claim_application(self,application):
        from ..server.task_runtime_setup import TaskRuntimeApplication
        if type(application) is not TaskRuntimeApplication or application.host is not self.host or application.runtime.executor is not self._pins[4]:
            raise ManualActionUnavailable('Exact original runtime application required')
        with self._condition:
            self.validate_bindings()
            if self._application is not None and self._application is not application:raise ManualActionUnavailable('Manual owner already claimed')
            self._application=application
        return True
    def _register(self,component,kind):
        from .expiry_observer import OwnedExpiryObserver
        from .owned_finalization import OwnedFinalization
        expected=OwnedExpiryObserver if kind=='observer' else OwnedFinalization
        if type(component) is not expected:raise ValueError('Exact manual component required')
        with component._tracking_lock:
            with self._condition:
                self.validate_bindings()
                if self._sealed or component._manual_started or component._manual_owner is not None or component in self._components:
                    raise ManualActionUnavailable('Manual component cannot be adopted or rebound')
                if len(self._components)>=self.max_components:raise ManualActionUnavailable('Manual inventory full')
                observer=component if kind=='observer' else component._observer
                h,k,s,m,handle,token,read,freeze=observer._pins
                if h is not self._pins[0] or m is not self._pins[1] or s.executor is not self._pins[4] or s.manager is not m or s.handle is not handle or handle.token is not token:
                    raise ManualActionUnavailable('Foreign manual owner')
                if kind=='finalizer' and (observer._manual_owner is not self or self._components.get(observer)!='observer'):
                    raise ManualActionUnavailable('Original observer must be registered first')
                component._manual_owner=self;component._manual_owner_pin=self
                self._components[component]=kind
        return True
    def register_observer(self,observer):return self._register(observer,'observer')
    def register_finalizer(self,finalizer):return self._register(finalizer,'finalizer')
    def _enter(self,component,operation):
        with self._condition:
            self.validate_bindings()
            kind=self._components.get(component)
            expected=('tick','claim_finalization') if kind=='observer' else ('run_once',) if kind=='finalizer' else ()
            if self._sealed or operation not in expected or component._manual_owner is not self or component._manual_owner_pin is not self:
                raise ManualActionUnavailable('Manual action admission closed')
            receipt=_Invocation(component,operation,get_ident());self._active[receipt]=component
            return receipt
    def _leave(self,receipt):
        with self._condition:
            if type(receipt) is not _Invocation or receipt.thread!=get_ident() or self._active.get(receipt) is not receipt.component:
                raise ManualActionUnavailable('Exact invocation completion required')
            component=receipt.component
            # Pure code-owned outcome inspection, no arbitrary callbacks.
            failed=bool(getattr(component,'_failed',False))
            retained=(getattr(component,'_handoff',None) is not None or getattr(component,'_freeze',None) is not None)
            if failed and retained:self._unresolved.add(component)
            del self._active[receipt];self._condition.notify_all()
    def _snapshot(self):return ManualActionSnapshot(len(self._active),len(self._unresolved),self._sealed)
    def snapshot(self):
        with self._condition:self.validate_bindings();return self._snapshot()
    def stop_new_actions(self):
        with self._condition:self.validate_bindings();self._sealed=True;self._condition.notify_all()
        return True
    def cancel_active(self):
        with self._condition:
            self.validate_bindings()
            if not self._sealed:raise ManualActionUnavailable('Seal manual admission before cancellation')
            components=tuple({c for c in self._active.values() if self._components[c]=='finalizer'})
        for component in components:
            try:component.cancel()
            except BaseException:
                with self._condition:self._unresolved.add(component)
        return self.snapshot()
    def wait(self,timeout):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<0:raise ValueError('Finite nonnegative wait required')
        with self._condition:
            self.validate_bindings()
            if not self._sealed:raise ManualActionUnavailable('Seal manual admission before waiting')
            self._condition.wait_for(lambda:not self._active,timeout)
            self.validate_bindings();return self._snapshot()
