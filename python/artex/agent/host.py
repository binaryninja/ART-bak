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

Full graph bootstrap/main/planner/workers orchestration remains absent. Nothing
here is automatically wired into HTTP routes, the CLI or application factory.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from threading import Condition, RLock
from time import monotonic

from ..manager import RuntimeUnavailable, TaskConflict, TaskManager
from .runner import AgentRunner


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


class AgentHost:
    def __init__(self, manager: TaskManager, runner: AgentRunner):
        if not isinstance(manager, TaskManager) or not isinstance(runner, AgentRunner) or manager.runner is not runner:
            raise RuntimeUnavailable('Host requires a TaskManager and its exact AgentRunner instance')
        self.manager, self.runner = manager, runner
        self._condition = Condition(RLock())
        self._mutations = 0
        self._draining = False
        self._settling = False

    def _invoke(self, callback, *, admission):
        with self._condition:
            if self._settling:
                raise TaskConflict('Lifecycle mutation cannot reenter completion settlement')
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

    def poll(self):
        """Non-background settlement pass. Busy is not evidence of no live work."""
        with self._condition:
            if self._mutations:
                return PollResult(busy=True)
            # Hold the admission fence through settlement; manager callbacks must
            # not reenter this host or start new work on another thread and wait.
            if self._settling:
                raise TaskConflict('Completion settlement cannot reenter itself')
            self._settling = True
            try:
                settled = tuple(self.runner.settle_completed(self.manager.complete))
                handles = self.runner.handles()
                running = sum(not handle.wait(0) for handle in handles)
                return PollResult(False, settled, running, len(handles) - running)
            finally:
                self._settling = False

    def drain(self, timeout):
        """Wait at most timeout for cooperative workers (excluding blocked stores).

        New admissions through this host are rejected during the call, not queued.
        Existing lifecycle mutations can finish, and pause remains available.
        A False result is not permission to discard handles or exit the process.
        """
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
            for handle in self.runner.handles():
                if not handle.wait(max(0, deadline - monotonic())):
                    state = self.poll()
                    return DrainResult(False, state)
            state = self.poll()
            return DrainResult(not state.busy and state.running == 0, state)
        finally:
            with self._condition:
                self._draining = False
                self._condition.notify_all()
