"""Explicit synchronous host lifecycle/settlement coordination.

All manager admission/control calls for this manager MUST pass through this host.
Calling manager.start/create/control or runner.settle_completed directly defeats
its admission fence. This is a host-owned coordination object, never a model tool.
No background poller, provider, tools or network are constructed. Call poll()
from the serving host periodically; bounded work can finish between host calls.

Lifecycle operations may overlap (so pause can cancel startup); settlement cannot
run until every hosted mutation has returned from the manager. A concurrent poll
returns busy rather than waiting or completing an uninstalled generation. Only
runner.settle_completed(manager.complete) commits terminal outcomes, preserving
the manager's exact-token and cancellation checks.

Drain temporarily rejects new admission, waits for admitted workers and result
sinks cooperatively and attempts settlement. It does not cancel, force-kill, mark
paused, or guarantee future inactivity once it returns. A timed-out drain leaves
work owned by the runner. Calls blocked in repositories/bootstrap/complete are
not forcibly interruptible; callback-level timeouts remain the host's duty.

This host does not bootstrap graphs or select an execution engine. An explicit
EngineRunner and hosted factory can connect initialized-task planner/worker cycles;
there is still no automatic HTTP/CLI wiring or background polling here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from threading import Condition, RLock
from time import monotonic

from ..manager import RuntimeUnavailable, TaskConflict, TaskManager
from typing import Protocol
from ..manager import Runner, RunHandle


class LifecycleRunner(Runner, Protocol):
    """Explicit runner lifecycle, with no implied engine completion semantics.

    handles() returns all retained exact-generation handles; settle_completed
    calls the supplied completion callback only for verified, drained outcomes.
    Neither method may forget unresolved work or infer success from model text.
    """
    def handles(self) -> tuple[RunHandle, ...]: ...
    def settle_completed(self, complete) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class PollResult:
    busy: bool
    settled: tuple[str, ...] = ()
    running: int = 0
    pending: int = 0

    @property
    def all_settled(self):
        return not self.busy and self.running == 0 and self.pending == 0


@dataclass(frozen=True)
class DrainResult:
    workers_drained: bool
    settlement: PollResult

    @property
    def all_settled(self):
        return self.workers_drained and self.settlement.all_settled


@dataclass(frozen=True)
class OwnedWorkResult:
    busy: bool
    value: object = None


class AgentHost:
    def __init__(self, manager: TaskManager, runner: LifecycleRunner):
        if not isinstance(manager, TaskManager) or manager.runner is not runner:
            raise RuntimeUnavailable('Host requires a TaskManager and its exact lifecycle runner')
        if any(not callable(getattr(runner,n,None)) for n in ('validate_create','ready','start','handles','settle_completed')):
            raise RuntimeUnavailable('Runner lacks required lifecycle methods')
        self.manager, self.runner = manager, runner
        self._condition = Condition(RLock())
        self._mutations = 0
        self._draining = False
        self._settling = False
        self._working = False
        claim=getattr(runner,'claim_host',None)
        if claim is not None:
            if not callable(claim):raise RuntimeUnavailable('Invalid runner host-claim capability')
            claim(self)

    def _identity(self):
        if self.manager.runner is not self.runner:
            raise RuntimeUnavailable('Hosted lifecycle runner identity changed')
        if any(not callable(getattr(self.runner,n,None)) for n in ('validate_create','ready','start','handles','settle_completed')):
            raise RuntimeUnavailable('Runner lifecycle protocol changed')
        claim=getattr(self.runner,'claim_host',None)
        if claim is not None:
            if not callable(claim):raise RuntimeUnavailable('Invalid runner host-claim capability')
            claim(self)

    def _handles(self):
        # Validate each inventory before using wait; never silently treat a bad
        # handle as drained. Inventory is an explicit finite runner contract.
        handles=self.runner.handles()
        self._identity()
        if not isinstance(handles,(list,tuple)) or len(handles)>100000:
            raise RuntimeUnavailable('Invalid runner handle inventory')
        if len({id(h) for h in handles})!=len(handles) or any(not callable(getattr(h,n,None)) for h in handles for n in ('cancel','wait')):
            raise RuntimeUnavailable('Invalid or duplicate runner handle')
        return tuple(handles)

    def _wait(self,handle,timeout):
        result=handle.wait(timeout)
        self._identity()
        if type(result) is not bool:raise RuntimeUnavailable('Runner handle wait must confirm a boolean drain result')
        return result

    def _invoke(self, callback, *, admission):
        self._identity()
        with self._condition:
            if self._settling:
                raise TaskConflict('Lifecycle mutation cannot reenter completion settlement')
            if admission and self._working:
                raise TaskConflict('Admission is suspended during an owned engine step')
            if admission and self._draining:
                raise TaskConflict('Admission is suspended during cooperative drain')
            self._mutations += 1
        try:
            result = callback()
        finally:
            with self._condition:
                self._mutations -= 1
                self._condition.notify_all()
        # Do not auto-poll here: a settlement-store failure must not masquerade
        # as a failed create/start and encourage a duplicate retry. Poll explicitly.
        return result

    def start(self, task_id, *, seed_first_intent=False):
        return self._invoke(lambda: self.manager.start(task_id, seed_first_intent=seed_first_intent), admission=True)

    def start_queued(self, task_id, expected_lifecycle):
        """Queue admission shares the ordinary start/drain/settlement fence."""
        return self._invoke(lambda: self.manager.start_queued(task_id, expected_lifecycle), admission=True)

    def create_task(self, description, goal, options=None, *, start=True, seed_first_intent=False):
        return self._invoke(lambda: self.manager.create_task(description, goal, options, start=start,
                            seed_first_intent=seed_first_intent), admission=True)

    def pause(self, task_id, *, timeout=5.0):
        result = self._invoke(lambda: self.manager.pause(task_id, timeout=timeout), admission=False)
        return result

    def control_task(self, task_id, action):
        if action == 'pause':
            return self.pause(task_id)
        if action != 'resume':
            raise ValueError('action must be pause|resume')
        return self._invoke(lambda: self.manager.control_task(task_id, action), admission=True)

    def advance_owned(self, callback):
        """Reserve one step over already admitted handles, never admit or settle.

        Reject overlap with admission, settlement or drain without waiting. Pause
        remains responsive while callback runs. Callback must return only after
        all work it owns has ceased; it must not create detached work. New
        admissions are rejected during this reservation, not silently queued.
        """
        if not callable(callback):raise ValueError('Owned work callback required')
        self._identity()
        with self._condition:
            if self._mutations or self._settling or self._draining or self._working:
                return OwnedWorkResult(True)
            self._working=True;self._mutations+=1
        try:
            self._identity()
            try:value=callback()
            finally:self._identity()
            return OwnedWorkResult(False,value)
        finally:
            with self._condition:
                self._working=False;self._mutations-=1
                self._condition.notify_all()

    def poll(self):
        """Non-background settlement pass. Busy is not evidence of no live work."""
        self._identity()
        with self._condition:
            if self._mutations:
                return PollResult(busy=True)
            # Hold the admission fence through settlement; manager callbacks must
            # not reenter this host or start new work on another thread and wait.
            if self._settling:
                raise TaskConflict('Completion settlement cannot reenter itself')
            self._settling = True
            try:
                settled = self.runner.settle_completed(self.manager.complete)
                self._identity()
                if not isinstance(settled, (list, tuple)) or len(settled)>100000 or any(not isinstance(item,str) or not item or len(item)>1024 for item in settled) or len(set(settled))!=len(settled):
                    raise RuntimeUnavailable('Invalid runner settlement receipts')
                settled = tuple(settled)
                handles = self._handles()
                running = sum(not self._wait(handle,0) for handle in handles)
                return PollResult(False, settled, running, len(handles) - running)
            finally:
                self._settling = False

    def drain(self, timeout):
        """Wait at most timeout for cooperative workers (excluding blocked stores).

        New admissions through this host are rejected during the call, not queued.
        Existing lifecycle mutations can finish, and pause remains available.
        A False result is not permission to discard handles or exit the process.
        """
        self._identity()
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('timeout must be finite and nonnegative')
        deadline = monotonic() + timeout
        with self._condition:
            if self._draining:
                raise TaskConflict('A drain is already in progress')
            self._draining = True
        try:
            with self._condition:
                while self._mutations:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return DrainResult(False, PollResult(True))
                    self._condition.wait(remaining)
            for handle in self._handles():
                if not self._wait(handle,max(0, deadline - monotonic())):
                    state = self.poll()
                    return DrainResult(False, state)
            state = self.poll()
            return DrainResult(not state.busy and state.running == 0, state)
        finally:
            with self._condition:
                self._draining = False
                self._condition.notify_all()
