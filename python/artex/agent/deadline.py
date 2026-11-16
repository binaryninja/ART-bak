"""Explicit prepared-generation planner/worker dispatch clock helper.

Not an installed runtime hook, timer or timeout coordinator. Call before_dispatch
ONLY at a final authorized planner/worker provider boundary, then enforce its
returned budget in the caller. Bootstrap goals/chat/side questions are ineligible.
Construction/preflight do no SQL. Source timeout=0 skips stamping entirely.
No task status changes, model execution, pause reset or generic generation IDs.
Existing engine/runner nonzero-timeout rejection remains unchanged.

First positive-budget dispatch stamps and registers exact manager ownership once;
later calls validate the same durable
clock read-only. A timestamp is admission to dispatch, not proof of network send.
Any ambiguous stamp or subsequent failed validation latches this helper for
reconciliation; never retry it to guess whether a write happened. Provider
integration must repeat cancellation/profile fences immediately before transport;
SQL and transport are not atomic. Absolute wall-clock deadline persists through
pauses. A local monotonic ceiling never expands on a backwards wall-clock jump.
Existing persisted deadlines are authoritative even if a legacy timeout setting
changed after stamping: source COALESCE preserves that deadline rather than
recomputing first_run_at + the new timeout. Corrupt clocks require reconciliation.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from threading import Lock
from time import monotonic
import json,math
from .runtime import Action,Grant,RunStopped
from ..sidequestion import Model
from ..db.task_deadline import TaskDeadlineStore,DeadlineReceipt

class DispatchClockUnavailable(RuntimeError):pass
class DispatchClockUncertain(DispatchClockUnavailable):pass
class TaskBudgetExpired(RunStopped):
    def __init__(self):super().__init__('task_deadline_expired')

@dataclass(frozen=True)
class DispatchBudget:
    deadline_at:datetime|None
    remaining_seconds:float|None
    monotonic_deadline:float|None
    task_limited:bool

@dataclass(frozen=True)
class GenerationClockObservation:
    lease:object
    expired:bool

class OwnedGenerationClock:
    """One original prepared owner shares one confirmed stamp across roles.

    This is persistence/clock coordination, not permission to execute. Callers
    must still use the role-specific final-dispatch grants. Construction is
    inert; existing leases cannot be adopted, and uncertain calls never retry.
    """
    def __init__(self,store,timeout_seconds,*,monotonic_clock=monotonic):
        if type(store) is not TaskDeadlineStore or type(timeout_seconds) is not int or not 0<=timeout_seconds<=2**31-1:raise ValueError('Actual store and bounded timeout required')
        if store.lease is not None:raise ValueError('Existing clock cannot be adopted')
        if not callable(monotonic_clock):raise ValueError('Explicit shared monotonic clock required')
        self.monotonic_clock=monotonic_clock;self._mono_pin=monotonic_clock;self._ceiling=None;self._last_mono=None
        self.store=store;self.timeout_seconds=timeout_seconds
        self._pins=(store,store.manager,store.handle,store.receipt,store.executor,store.guard,timeout_seconds)
        self._lock=Lock();self._clock=None;self._lease=None;self._failed=False
    @property
    def unresolved(self):return self._failed
    def _check(self):
        s,m,h,r,e,g,t=self._pins
        if self.monotonic_clock is not self._mono_pin or self.store is not s or self.timeout_seconds!=t or any(a is not b for a,b in zip((s.manager,s.handle,s.receipt,s.executor,s.guard),(m,h,r,e,g))) or h.prepared_timeout_seconds!=t or s.lease is not self._lease:
            raise DispatchClockUnavailable('Shared clock ownership changed')
    def narrow_deadline(self,candidate,now):
        """Generation-wide nonexpanding ceiling in the one declared clock."""
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (candidate,now)):raise DispatchClockUnavailable('Invalid shared clock sample')
        with self._lock:
            self._check()
            if self._failed:raise DispatchClockUncertain('Shared clock needs reconciliation')
            if self._last_mono is not None and now<self._last_mono:raise DispatchClockUnavailable('Shared monotonic clock moved backwards')
            self._last_mono=now
            self._ceiling=candidate if self._ceiling is None else min(self._ceiling,candidate)
            return self._ceiling
    def observe(self):
        """Non-stamping original-clock snapshot; None means stamp currently busy."""
        if not self._lock.acquire(False):return None
        try:
            self._check()
            if self._failed:raise DispatchClockUncertain('Shared clock needs reconciliation')
            if self._lease is None:return GenerationClockObservation(None,False)
            now=self._mono_pin()
            if isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now) or now<0 or self._last_mono is not None and now<self._last_mono:
                raise DispatchClockUnavailable('Invalid observation clock')
            self._last_mono=now;self._check()
            return GenerationClockObservation(self._lease,self._ceiling is not None and now>=self._ceiling)
        finally:self._lock.release()

    def read_or_stamp(self):
        if not self._lock.acquire(False):raise DispatchClockUnavailable('Shared clock dispatch already active')
        attempted=False
        try:
            if self._failed:raise DispatchClockUncertain('Shared clock needs reconciliation')
            self._check()
            if self.timeout_seconds==0:return None
            attempted=True
            if self._clock is None:
                value=self.store.stamp_owned(self.timeout_seconds)
                lease=self.store.lease
                if lease is None or lease.clock is not value:raise DispatchClockUncertain('Exact installed clock missing')
                self._lease=lease;self._clock=value
            else:
                value=self.store.validate_current(self.timeout_seconds)
                if value!=self._clock:raise DispatchClockUncertain('Shared clock changed')
            self._check();return self._clock
        except BaseException:
            if attempted:self._failed=True
            raise
        finally:self._lock.release()

class PreparedDispatchClock:
    def __init__(self,store,*,model,role,timeout_seconds,authorize,binding_current,
                 wall_clock=lambda:datetime.now(timezone.utc),monotonic_clock=monotonic,generation_clock=None):
        if type(store) is not TaskDeadlineStore or not isinstance(model,Model) or not model.identity:raise ValueError('Actual prepared deadline store and identified model required')
        if role not in ('planner','worker') or type(timeout_seconds) is not int or not 0<=timeout_seconds<=2**31-1:raise ValueError('Explicit planner/worker timeout required')
        if any(not callable(f) for f in (authorize,binding_current,wall_clock,monotonic_clock)):raise ValueError('Explicit dispatch policy and clocks required')
        if generation_clock is not None and (type(generation_clock) is not OwnedGenerationClock or generation_clock.store is not store or generation_clock.timeout_seconds!=timeout_seconds or generation_clock.monotonic_clock is not monotonic_clock):raise ValueError('Exact shared generation clock required')
        self.generation_clock=generation_clock;self._generation_clock=generation_clock
        self.store,self.model,self.role,self.timeout_seconds=store,model,role,timeout_seconds
        self.authorize,self.binding_current=authorize,binding_current
        self.wall_clock,self.monotonic_clock=wall_clock,monotonic_clock
        self._policy_pins=(authorize,binding_current,wall_clock,monotonic_clock)
        self._store,self._model,self._role,self._timeout=store,model,role,timeout_seconds
        self._manager,self._handle,self._receipt,self._executor,self._guard=store.manager,store.handle,store.receipt,store.executor,store.guard
        scope=store.handle.scope
        if scope is None:raise ValueError('Prepared generation required')
        self._identity=dict(task_id=scope.task_id,exploration_id=scope.exploration_id,run_id=scope.run_id,revision=scope.revision,role=role)
        self._lock=Lock();self._clock=None;self._failed=False;self._mono_deadline=None;self._last_mono=None
    @property
    def unresolved(self):return self._failed
    def _check(self,context):
        if not isinstance(context,dict) or not callable(getattr(context,'check',None)):raise DispatchClockUnavailable('Exact runtime context required')
        context.check()
        if any(a is not b for a,b in zip((self.authorize,self.binding_current,self.wall_clock,self.monotonic_clock),self._policy_pins)):raise DispatchClockUnavailable('Dispatch clock policy changed')
        if self.generation_clock is not self._generation_clock:raise DispatchClockUnavailable('Shared clock changed')
        if self._generation_clock is not None:self._generation_clock._check()
        if (self.store is not self._store or self.model!=self._model or self.role!=self._role or self.timeout_seconds!=self._timeout
            or self.store.manager is not self._manager or self.store.handle is not self._handle or self.store.receipt is not self._receipt
            or self.store.executor is not self._executor or self.store.guard is not self._guard):raise DispatchClockUnavailable('Dispatch clock binding changed')
        if self._handle.prepared_timeout_seconds!=self._timeout:raise DispatchClockUnavailable('Prepared timeout configuration mismatch')
        for key,value in self._identity.items():
            if type(context.get(key)) is not type(value) or context[key]!=value:raise DispatchClockUnavailable('Dispatch generation mismatch')
        if self._handle.validate_prepared_receipt(self._receipt) is not True or self._manager.owns_generation(str(self._receipt.task_id),self._handle.token,self._handle) is not True:raise DispatchClockUnavailable('Prepared dispatch owner unavailable')
        if self._handle.coordinator.guard is not self._guard:raise DispatchClockUnavailable('Dispatch lifetime changed')
        scope=self._handle.scope
        if (scope.task_id,scope.exploration_id,scope.run_id,scope.revision)!=(self._identity['task_id'],self._identity['exploration_id'],self._identity['run_id'],self._identity['revision']):raise DispatchClockUnavailable('Dispatch configuration changed')
        context.check();self._handle.token.check()
    def validate_dispatch(self,context):
        """Read-only per-chunk binding fence; no stamp, SQL or new grant."""
        self._check(context)
        if self._failed or self._generation_clock is not None and self._generation_clock.unresolved:raise DispatchClockUncertain('Dispatch clock requires reconciliation')
        if self.binding_current(self._model) is not True:raise DispatchClockUnavailable('Provider binding unavailable')
        self._check(context);return True
    def _validate_receipt(self,value):
        r=self._receipt
        if type(value) is not DeadlineReceipt or (value.task_id,value.exploration_id,value.run_id,value.journal_revision,value.timeout_seconds)!=(r.task_id,r.exploration_id,r.run_id,r.revision,self._timeout):raise DispatchClockUncertain('Clock receipt identity mismatch')
        for date in (value.first_run_at,value.deadline_at):
            if not isinstance(date,datetime) or date.tzinfo is None or date.utcoffset() is None:raise DispatchClockUncertain('Aware persisted clock required')
        if value.deadline_at<value.first_run_at:raise DispatchClockUncertain('Invalid persisted clock order')
        if self._clock is not None and value!=self._clock:raise DispatchClockUncertain('Persisted task clock changed')
    def before_dispatch(self,context,request,*,own_budget_seconds=None):
        if own_budget_seconds is not None and (isinstance(own_budget_seconds,bool) or not isinstance(own_budget_seconds,(int,float)) or not math.isfinite(own_budget_seconds) or own_budget_seconds<=0):raise ValueError('Own budget must be positive or None, never zero-as-unlimited')
        if not isinstance(request,dict):raise ValueError('Explicit provider request required')
        encoded=json.dumps(request,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(encoded.encode('utf-8'))>1048576:raise ValueError('Dispatch request exceeds bound')
        if not self._lock.acquire(False):raise DispatchClockUnavailable('Clock dispatch already active')
        attempted=False
        try:
            if self._failed:raise DispatchClockUncertain('Clock requires reconciliation')
            with self._guard(self._receipt.task_id,self._receipt.exploration_id):
                self._check(context)
                grant=self.authorize(context,Action('model','complete',encoded))
                self._check(context)
                if not isinstance(grant,Grant) or not all(v is True for v in (grant.permission,grant.in_scope,grant.approved)):raise RunStopped('permission_denied')
                if self.binding_current(self._model) is not True:raise DispatchClockUnavailable('Provider binding unavailable')
                self._check(context)
            # Store operations own this same guard themselves; do not nest a
            # potentially non-reentrant lifetime guard around their transaction.
            if self._timeout:
                attempted=True
                value=self._generation_clock.read_or_stamp() if self._generation_clock is not None else self._store.stamp_owned(self._timeout) if self._clock is None else self._store.validate_current(self._timeout)
                self._validate_receipt(value);self._clock=value
            with self._guard(self._receipt.task_id,self._receipt.exploration_id):
                # SQL/policy callbacks may block or invalidate the generation.
                self._check(context)
                grant=self.authorize(context,Action('model','complete',encoded))
                self._check(context)
                if not isinstance(grant,Grant) or not all(v is True for v in (grant.permission,grant.in_scope,grant.approved)):raise RunStopped('permission_denied')
                if self.binding_current(self._model) is not True:raise DispatchClockUnavailable('Provider binding changed')
                self._check(context)
                mono=self.monotonic_clock()
                if isinstance(mono,bool) or not isinstance(mono,(int,float)) or not math.isfinite(mono) or mono<0 or (self._last_mono is not None and mono<self._last_mono):raise DispatchClockUnavailable('Invalid monotonic clock')
                self._last_mono=mono
                deadline=None;remaining=own_budget_seconds;limited=False
                if self._timeout:
                    wall=self.wall_clock()
                    if not isinstance(wall,datetime) or wall.tzinfo is None or wall.utcoffset() is None:raise DispatchClockUnavailable('Aware wall clock required')
                    deadline=self._clock.deadline_at
                    seconds=(deadline-wall).total_seconds()
                    candidate=mono+seconds
                    self._mono_deadline=candidate if self._mono_deadline is None else min(self._mono_deadline,candidate)
                    if self._generation_clock is not None:self._mono_deadline=self._generation_clock.narrow_deadline(self._mono_deadline,mono)
                    seconds=min(seconds,self._mono_deadline-mono)
                    if seconds<=0:raise TaskBudgetExpired()
                    limited=remaining is None or seconds<=remaining
                    remaining=seconds if remaining is None else min(seconds,remaining)
                self._check(context)
                if self.binding_current(self._model) is not True:raise DispatchClockUnavailable('Provider binding changed before dispatch')
                self._check(context)
                # The final profile/current fences may block. Preserve the
                # earlier own/task ceiling, then charge their elapsed time.
                ceiling=None if remaining is None else mono+remaining
                final_wall=self.wall_clock() if self._timeout else None
                final_mono=self.monotonic_clock()
                if isinstance(final_mono,bool) or not isinstance(final_mono,(int,float)) or not math.isfinite(final_mono) or final_mono<mono:raise DispatchClockUnavailable('Invalid final monotonic clock')
                self._last_mono=final_mono
                if self._timeout:
                    if not isinstance(final_wall,datetime) or final_wall.tzinfo is None or final_wall.utcoffset() is None:raise DispatchClockUnavailable('Aware final wall clock required')
                    self._mono_deadline=min(self._mono_deadline,final_mono+(deadline-final_wall).total_seconds())
                    if self._generation_clock is not None:self._mono_deadline=self._generation_clock.narrow_deadline(self._mono_deadline,final_mono)
                    ceiling=self._mono_deadline if ceiling is None else min(ceiling,self._mono_deadline)
                    if self._mono_deadline<=final_mono:raise TaskBudgetExpired()
                    limited=own_budget_seconds is None or self._mono_deadline<=mono+own_budget_seconds
                remaining=None if ceiling is None else ceiling-final_mono
                if remaining is not None and remaining<=0:raise RunStopped('timeout')
                # Clock callbacks are host code too. Pure final pins and the
                # actual manager token fence follow sampling, without another
                # callback/clock measurement that could replenish the budget.
                if any(a is not b for a,b in zip((self.authorize,self.binding_current,self.wall_clock,self.monotonic_clock),self._policy_pins)) or self.generation_clock is not self._generation_clock:raise DispatchClockUnavailable('Final dispatch binding changed')
                if self._generation_clock is not None:self._generation_clock._check()
                if self._handle.token.cancelled or not self._manager.owns_generation(str(self._receipt.task_id),self._handle.token,self._handle):raise DispatchClockUnavailable('Final dispatch owner revoked')
                return DispatchBudget(deadline,remaining,ceiling,limited)
        except TaskBudgetExpired:raise
        except BaseException:
            if attempted:self._failed=True
            raise
        finally:self._lock.release()
