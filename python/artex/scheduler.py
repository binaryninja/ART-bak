"""Explicit tick-driven FIFO admission coordinator; no threads or automatic I/O.

Source: server/goals.go reconcileConcurrency and engine_timeout.go. Queue order is
queued_at, falling back to created_at, then numeric task ID. Paused/deleted/terminal
rows cannot start; unavailable providers do not block ready tasks behind them.
Stamped absolute deadlines continue through pauses. This does NOT stamp first-run
clocks, revive terminal tasks, synthesize bootstrap modes, run final planners, or
implement event/interval triggers from Go's separate Scheduler.

Integration is deliberately explicit: LocalHostAdapter connects the public
AgentHost/TaskManager queued-admission gate. Deadline settlement remains absent.
Alternative host adapters must use the SAME manager generation gate,
DB compare-and-set and cooperative cancellation/drain machinery. Never implement
it as dequeue followed by manager.start: that can race pause/delete/re-enqueue.
A successful timeout receipt means final planning/drain/guarded terminal persistence
are complete, not just that cancellation was requested. Missing adapters fail closed.

ALL admissions must pass through this scheduler; concurrent direct host.start
or host.create_task calls bypass its capacity accounting and are unsupported.
This object's lock prevents duplicate local ticks/admissions, not multi-process
races. Atomic generation/queue/deletion checks are the host adapter's obligation;
there is no claim that existing status-only SQL CAS provides that guarantee.
All callbacks must be bounded by their owner; tick work count is bounded but Python
cannot preempt a blocked store/provider adapter. No network or tools are constructed.
"""
from dataclasses import dataclass
from math import isfinite
from threading import Lock
from typing import Protocol

from .manager import RuntimeUnavailable

TERMINAL=frozenset(('done','failed','timeout'))


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: int
    generation: str  # opaque immutable admission generation supplied by host/store
    created_at: float
    queued_at: float|None=None
    status: str='running'
    queued: bool=False
    paused: bool=False
    deleted: bool=False
    queue_mode: str=''
    deadline_at: float|None=None

    def __post_init__(self):
        if type(self.task_id) is not int or not 0<self.task_id<2**63: raise ValueError('invalid task ID')
        if not isinstance(self.generation,str) or not self.generation: raise ValueError('generation required')
        for key in ('queued','paused','deleted'):
            if type(getattr(self,key)) is not bool: raise ValueError('invalid lifecycle flag')
        for key in ('created_at','queued_at','deadline_at'):
            value=getattr(self,key)
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or value<0):
                raise ValueError('invalid lifecycle timestamp')
        if self.created_at is None: raise ValueError('creation time required')
        if not isinstance(self.status,str) or not isinstance(self.queue_mode,str): raise ValueError('invalid lifecycle text')


@dataclass(frozen=True)
class TickResult:
    busy: bool=False
    admitted: tuple[int,...]=()
    settled_deadlines: tuple[int,...]=()
    deferred: tuple[int,...]=()


class SnapshotStore(Protocol):
    def scheduler_snapshot(self,limit:int)->tuple[list[TaskSnapshot],bool]:
        """Return all relevant rows and complete flag. Never silently truncate."""
        ...


class SchedulerHost(Protocol):
    def poll(self):
        """AgentHost PollResult; running+pending reserve slots until settlement."""
        ...
    def ready(self,snapshot:TaskSnapshot)->bool: ...
    def admit_queued(self,snapshot:TaskSnapshot)->str:
        """Return admitted/stale/deferred. Under SAME host gate: recheck exact
        generation, queued/unpaused/nondeleted, DB CAS and install run handle.
        On ambiguous launch failure retain ownership and capacity until drained.
        Never acknowledge admitted for a queue-only persistence change."""
        ...
    def settle_deadline(self,snapshot:TaskSnapshot,now:float)->str:
        """Return settled/stale/deferred. Generation-safe cooperative drain,
        final planner and guarded terminal commit required for settled."""
        ...
    def drain(self,timeout:float): ...


class FIFOScheduler:
    def __init__(self,store:SnapshotStore,host:SchedulerHost,*,clock,limit=1,max_scan=10000,max_actions=100):
        for value in (limit,max_scan,max_actions):
            if type(value) is not int or value<=0: raise ValueError('scheduler bounds must be positive integers')
        if not callable(clock): raise ValueError('explicit clock required')
        for object,names in ((store,('scheduler_snapshot',)),(host,('poll','ready','admit_queued','settle_deadline','drain'))):
            if any(not callable(getattr(object,name,None)) for name in names):
                raise RuntimeUnavailable('Scheduler requires explicit generation-safe host/store adapters')
        self.store,self.host,self.clock=store,host,clock
        self.limit,self.max_scan,self.max_actions=limit,max_scan,max_actions
        self._lock=Lock();self._draining=False
        self._receipts=set()

    def tick(self):
        if not self._lock.acquire(blocking=False): return TickResult(busy=True)
        try:
            if self._draining: return TickResult(busy=True)
            now=self.clock()
            if isinstance(now,bool) or not isinstance(now,(int,float)) or not isfinite(now): raise ValueError('invalid clock')
            state=self.host.poll()
            if state.busy: return TickResult(busy=True)
            if any(type(value) is not int or value<0 for value in (state.running,state.pending)):
                raise RuntimeUnavailable('Invalid host occupancy')
            rows,complete=self.store.scheduler_snapshot(self.max_scan)
            if complete is not True or not isinstance(rows,(list,tuple)) or len(rows)>self.max_scan:
                raise RuntimeUnavailable('Complete bounded lifecycle snapshot required')
            if any(not isinstance(row,TaskSnapshot) for row in rows) or len({r.task_id for r in rows})!=len(rows):
                raise RuntimeUnavailable('Invalid lifecycle snapshot')
            # Retain receipts while that exact generation is still visible, even
            # if a lagging snapshot incorrectly reports it queued after admission.
            live={(r.task_id,r.generation) for r in rows}
            self._receipts.intersection_update(live)
            admitted=[];settled=[];deferred=[];actions=0
            expired={r.task_id for r in rows if not r.deleted and r.status not in TERMINAL and r.deadline_at and r.deadline_at<=now}
            for row in sorted(rows,key=lambda r:(r.deadline_at or float('inf'),r.task_id)):
                if row.task_id not in expired or actions>=self.max_actions: continue
                actions+=1
                outcome=self.host.settle_deadline(row,now)
                if outcome=='settled': settled.append(row.task_id)
                elif outcome=='deferred': deferred.append(row.task_id)
                elif outcome!='stale': raise RuntimeUnavailable('Invalid deadline receipt')
            # Do not assume timed-out work freed a slot: refresh exact host ownership.
            state=self.host.poll()
            if state.busy: return TickResult(True,tuple(admitted),tuple(settled),tuple(deferred))
            if any(type(value) is not int or value<0 for value in (state.running,state.pending)):
                raise RuntimeUnavailable('Invalid host occupancy')
            slots=max(0,self.limit-state.running-state.pending)
            queue=sorted((r for r in rows if r.queued),key=lambda r:(r.queued_at or r.created_at,r.task_id))
            for row in queue:
                if slots<=0 or actions>=self.max_actions: break
                if row.deleted or row.paused or row.status in TERMINAL or row.task_id in expired: continue
                receipt=(row.task_id,row.generation)
                if receipt in self._receipts: continue
                actions+=1
                if row.queue_mode not in ('bootstrap','resume'):
                    deferred.append(row.task_id);continue  # no inferred graph-bootstrap state
                readiness=self.host.ready(row)
                if type(readiness) is not bool: raise RuntimeUnavailable('Invalid provider readiness')
                if not readiness: deferred.append(row.task_id);continue
                outcome=self.host.admit_queued(row)
                if outcome=='admitted':
                    self._receipts.add(receipt);admitted.append(row.task_id);slots-=1
                elif outcome=='deferred': deferred.append(row.task_id)
                elif outcome!='stale': raise RuntimeUnavailable('Invalid admission receipt')
                # A deferred/failed launch may still own a cancel-gated handle.
                # Never infer capacity release merely from its receipt string.
                state=self.host.poll()
                if state.busy:
                    return TickResult(True,tuple(admitted),tuple(settled),tuple(deferred))
                if any(type(value) is not int or value<0 for value in (state.running,state.pending)):
                    raise RuntimeUnavailable('Invalid host occupancy')
                slots=max(0,self.limit-state.running-state.pending)
            return TickResult(False,tuple(admitted),tuple(settled),tuple(deferred))
        finally: self._lock.release()

    def drain(self,timeout):
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not isfinite(timeout) or timeout<0:
            raise ValueError('timeout must be finite and nonnegative')
        if not self._lock.acquire(blocking=False):
            raise RuntimeUnavailable('Scheduler tick is busy; drain was not started')
        try:
            if self._draining: raise RuntimeUnavailable('Scheduler drain already in progress')
            self._draining=True
        finally: self._lock.release()
        try: return self.host.drain(timeout)
        finally:
            with self._lock: self._draining=False


def _snapshot(row):
    """Opaque local-registry fingerprint. Not a DB revision or distributed lease."""
    from datetime import datetime
    import hashlib
    import json
    def stamp(value):
        if value is None: return None
        if not isinstance(value,datetime) or value.tzinfo is None:
            raise RuntimeUnavailable('Scheduler timestamps must be timezone-aware datetimes')
        return value.timestamp()
    def encode(value):
        if isinstance(value,datetime): return value.isoformat()
        raise TypeError('Unsupported lifecycle fingerprint value')
    generation=hashlib.sha256(json.dumps(row,sort_keys=True,default=encode,allow_nan=False).encode()).hexdigest()
    return TaskSnapshot(int(row['id']),generation,stamp(row['created_at']),stamp(row.get('queued_at')),
                        row.get('status',''),row.get('queued',False),row.get('paused',False),
                        bool(row.get('deleted_at')),row.get('queue_mode') or '',stamp(row.get('deadline_at')))


class LocalHostAdapter:
    """Connect AgentHost to FIFO ticks in a single-process ownership regime.

    Construct with single_process=True only when ALL lifecycle writers use this
    exact host/manager and other processes cannot change task state. The backing
    status-only SQL CAS is insufficient for distributed queue generations.
    Snapshot rows come from the loaded manager registry, not fresh DB reads.
    Deadline final-planner settlement is not ported: expired tasks are deferred,
    never marked timeout/done and never admitted. Already-running work is not
    forcibly cancelled; caller must not treat this as deadline enforcement.
    """
    def __init__(self,host,*,single_process=False):
        from .agent.host import AgentHost
        if not isinstance(host,AgentHost) or single_process is not True:
            raise RuntimeUnavailable('Explicit single-process AgentHost ownership required')
        self.host=host
    def scheduler_snapshot(self,limit):
        rows,complete=self.host.manager.scheduler_snapshot_rows(limit)
        return [_snapshot(row) for row in rows],complete
    def poll(self): return self.host.poll()
    def ready(self,snapshot):
        from .manager import TaskNotFound
        try:
            row=self.host.manager.lifecycle_snapshot(str(snapshot.task_id))
            if _snapshot(row).generation!=snapshot.generation: return False
            return self.host.runner.ready(row)
        except (RuntimeUnavailable,TaskNotFound):
            return False
    def admit_queued(self,snapshot):
        from .manager import TaskConflict,TaskNotFound
        try:
            row=self.host.manager.lifecycle_snapshot(str(snapshot.task_id))
            if _snapshot(row).generation!=snapshot.generation: return 'stale'
            return 'admitted' if self.host.start_queued(str(snapshot.task_id),row) else 'deferred'
        except (TaskConflict,TaskNotFound): return 'stale'
        except RuntimeUnavailable: return 'deferred'
    def settle_deadline(self,snapshot,now): return 'deferred'
    def drain(self,timeout): return self.host.drain(timeout)
