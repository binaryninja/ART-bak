"""Opt-in worker foreground process composition over an explicit host runner.

No runner, shell, environment, permission, process or thread is supplied by
this module. A runner's valid return contracts that its effects have drained;
this is not OS containment or proof about detached descendants. Runners must
cooperate with context cancellation and bound their own blocking operations.
Exceptions after runner entry are sticky uncertainty, including cancellation.
The independent ledger cannot be cleared by canonical transcript settlement.
No automatic retry/reconciliation, background jobs or stored scripts.
"""
from dataclasses import dataclass
import os
from threading import RLock, get_ident
from .runtime import EffectUncertain, Grant, RunStopped
from ..tools.process import BashTool, ProcessResult


class ProcessBindingUnavailable(RuntimeError):pass


@dataclass(frozen=True)
class WorkerProcessPolicy:
    revision: str
    root: str
    shell: str
    environment: tuple
    runner: object
    authorize: object
    max_output_bytes: int = 65536
    max_timeout_ms: int = 600000

    def __post_init__(self):
        if type(self.revision) is not str or not 1 <= len(self.revision) <= 128:
            raise ValueError('Explicit process policy revision required')
        if type(self.environment) is not tuple or any(type(p) is not tuple or len(p)!=2 for p in self.environment):
            raise ValueError('Immutable explicit environment pairs required')
        if len(dict(self.environment)) != len(self.environment):raise ValueError('Duplicate environment key')
        BashTool(self.root,shell=self.shell,environment=dict(self.environment),gate=self.authorize,
            runner=self.runner,max_output_bytes=self.max_output_bytes,max_timeout_ms=self.max_timeout_ms)

    def configuration(self):
        # Environment values may be secrets; this object is only hashed locally.
        return {k:v for k,v in vars(self).items() if k not in ('runner','authorize')}


def _identity(s):return (s.task_id,s.exploration_id,s.run_id,s.revision)


class PreparedWorkerProcess:
    def __init__(self,scope,policy,*,configuration_current):
        from .engine import EngineScope
        if type(scope) is not EngineScope or type(policy) is not WorkerProcessPolicy or not callable(configuration_current):
            raise ValueError('Exact process scope, policy and authority required')
        policy.__post_init__()
        self.scope,self.policy,self.configuration_current=scope,policy,configuration_current
        self._pins=(scope,policy,configuration_current,tuple(vars(policy).items()))
        st=os.stat(policy.root);self._root_identity=(st.st_dev,st.st_ino)
        self._bound=None;self._attempted=False;self._lock=RLock();self._active=0;self._uncertain=False

    def validate_bindings(self):
        s,p,c,fields=self._pins
        if self.scope is not s or self.policy is not p or self.configuration_current is not c or any(getattr(p,k) is not v for k,v in fields):
            raise ProcessBindingUnavailable('Process policy changed')
        st=os.stat(p.root)
        if os.path.realpath(p.root)!=p.root or (st.st_dev,st.st_ino)!=self._root_identity:raise ProcessBindingUnavailable('Process working directory changed')
        if self._bound is not None:
            manager,outer,engine,worker,token,receipt,cleanup,executor=self._bound
            if (outer.manager is not manager or outer._child is not engine or outer.token is not token
                or outer._receipt is not receipt or engine.scope is not s or engine.worker is not worker
                or engine.token is not token or engine.cleanup is not cleanup or worker.cleanup is not cleanup
                or outer.executor is not executor or engine.store.executor is not executor
                or manager.repository.executor is not executor):raise ProcessBindingUnavailable('Process owner changed')
        return True

    def bind(self,manager,outer,engine):
        from .engine import TaskEngineHandle
        from .bootstrap_handle import BootstrapHandle
        from ..manager import TaskManager
        with self._lock:
            self.validate_bindings()
            if self._attempted:raise ProcessBindingUnavailable('Process binding cannot retry')
            self._attempted=True
            if type(manager) is not TaskManager or type(outer) is not BootstrapHandle or type(engine) is not TaskEngineHandle:
                raise ProcessBindingUnavailable('Exact owned engine required')
            if (outer.manager is not manager or outer._child is not engine or outer.phase!='prepared'
                or engine.scope is not self.scope or outer.scope!=self.scope or outer.token is not engine.token
                or engine.cleanup is None or outer.token.cancelled or engine._active or engine._started
                or manager.owns_generation(outer.task_id,outer.token,outer) is not True
                or outer.validate_prepared_receipt(outer._receipt) is not True):
                raise ProcessBindingUnavailable('Inert prepared cleanup owner required')
            self._bound=(manager,outer,engine,engine.worker,outer.token,outer._receipt,engine.cleanup,outer.executor)
            self.validate_bindings()

    def drained(self):
        self.validate_bindings()
        with self._lock:return self._active==0 and not self._uncertain

    def unresolved(self):
        self.validate_bindings()
        with self._lock:return self._uncertain

    def _fence(self,scope,context):
        self.validate_bindings();context.check()
        if self._bound is None:raise ProcessBindingUnavailable('Process owner unbound')
        manager,outer,engine,worker,token,*_=self._bound
        if (_identity(scope)!=_identity(self.scope) or not outer._active or outer._thread!=get_ident()
            or outer.phase!='engine' or not engine._active or engine._thread!=get_ident()
            or engine._child!=('worker',scope.intent_id) or manager.owns_generation(outer.task_id,token,outer) is not True):
            raise ProcessBindingUnavailable('Original active worker required')
        for k,v in scope.identity(worker.limits).items():
            if type(context.get(k)) is not type(v) or context.get(k)!=v:raise ProcessBindingUnavailable('Process context changed')
        worker.check(scope,'tool',claimed=True)
        if self.configuration_current(scope) is not True:raise ProcessBindingUnavailable('Process configuration revoked')
        context.check();self.validate_bindings();worker.check(scope,'tool',claimed=True)
        context.check();self.validate_bindings()

    def tools(self,scope):
        from .worker import WorkerScope
        if type(scope) is not WorkerScope or _identity(scope)!=_identity(self.scope):raise ProcessBindingUnavailable('Exact worker scope required')
        self.validate_bindings()
        p=self.policy
        def authorize(context,launch):
            self._fence(scope,context)
            grant=p.authorize(context,launch)
            self._fence(scope,context)
            return grant
        def runner(context,launch):
            self._fence(scope,context)
            # Lifecycle/config callbacks above can revoke an earlier launch
            # grant. Refresh exact launch authority last, then only check
            # cancellation and original resource pins before admitting effects.
            # This is a finite boundary, not a cyclic policy fixed point.
            grant=p.authorize(context,launch)
            context.check();self.validate_bindings()
            # Worker-local cancellation is distinct from manager generation
            # cancellation; the last grant may cancel the original owner.
            manager,outer,engine,worker,token,*_=self._bound
            if token.cancelled or manager.owns_generation(outer.task_id,token,outer) is not True:
                raise RunStopped('cancelled')
            if not isinstance(grant,Grant) or not all(v is True for v in
                (grant.permission,grant.in_scope,grant.approved)):
                raise RunStopped('permission_denied')
            with self._lock:
                if self._active or self._uncertain:raise EffectUncertain('Process owner requires reconciliation')
                self._active=1
            try:
                result=p.runner(context,launch)
                if (type(result) is not ProcessResult or type(result.output) is not bytes
                    or len(result.output)>launch.max_output_bytes or type(result.exit_code) is not int
                    or type(result.timed_out) is not bool or type(result.output_limited) is not bool):
                    raise EffectUncertain('Process drain result invalid')
                self.validate_bindings()
                return result
            except BaseException:
                with self._lock:self._uncertain=True
                raise EffectUncertain('Process effects require reconciliation') from None
            finally:
                with self._lock:self._active=0
        return BashTool(p.root,shell=p.shell,environment=dict(p.environment),gate=authorize,
            runner=runner,max_output_bytes=p.max_output_bytes,max_timeout_ms=p.max_timeout_ms).tools()
