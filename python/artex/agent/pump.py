"""Opt-in bounded tick loop for already owned initialized EngineRunner handles.

Construction starts nothing. tick() is synchronous; start() explicitly creates
one non-daemon pump thread. Neither admits tasks nor supplies providers/tools.
One tick advances at most max_per_tick handles, rotating fairly. EngineHandle
owns source heartbeat gating (minimum 600s); interval is polling latency, not a
new planner heartbeat. Completion remains fresh persisted-goal host settlement.

stop(timeout) only stops this pump and waits for its actual thread/callbacks; it
never cancels user tasks. False retains ownership and prohibits restart while
callbacks remain. To pause a task, explicitly use the host lifecycle API. No
force kill or implicit task completion. External callers must not tick the same
runner independently. One live pump per host is enforced in this process.
Any unexpected callback error permanently quarantines the pump (generic failure
label, no payload logging). Reconcile effects before constructing another host;
no automatic retries or background recovery. Unresolved handles are skipped.
"""
from dataclasses import dataclass
from threading import Condition,RLock,Lock,Thread,Event,current_thread
from time import monotonic
from weakref import WeakKeyDictionary,ref
import math
from .host import AgentHost
from .engine_runner import EngineRunner
from ..manager import RuntimeUnavailable

@dataclass(frozen=True)
class TickResult:
    busy:bool=False
    advanced:tuple=()
    settlement:object=None
    stopped:bool=False

_claim_lock=Lock()
_claims=WeakKeyDictionary()

class EnginePump:
    def __init__(self,host,*,interval=1.0,max_per_tick=8,clock=monotonic):
        if not isinstance(host,AgentHost) or not isinstance(host.runner,EngineRunner):raise ValueError('Exact hosted EngineRunner required')
        if isinstance(interval,bool) or not isinstance(interval,(int,float)) or not math.isfinite(interval) or not .01<=interval<=60:raise ValueError('Interval must be 0.01..60 seconds')
        if type(max_per_tick) is not int or not 1<=max_per_tick<=1000 or not callable(clock):raise ValueError('Bounded tick count and clock required')
        self.host,self.runner,self.manager=host,host.runner,host.manager
        self.runner.claim_host(host)
        with _claim_lock:
            old=_claims.get(host)
            if old is not None and old() is not None:raise RuntimeUnavailable('Host already has a live engine pump')
            _claims[host]=ref(self)
        self.interval,self.max_per_tick,self.clock=interval,max_per_tick,clock
        self._condition=Condition(RLock());self._tick_lock=Lock();self._stop=Event()
        self._thread=None;self._active=0;self._failure='';self._cursor=0;self._stoppers=0
    @property
    def failure(self):
        with self._condition:return self._failure
    def _identity(self):
        if self.host.runner is not self.runner or self.host.manager is not self.manager or self.manager.runner is not self.runner:raise RuntimeUnavailable('Pump lifecycle identity changed')
        self.runner.claim_host(self.host)
    def tick(self):
        if not self._tick_lock.acquire(False):return TickResult(busy=True)
        try:
            with self._condition:
                if self._stop.is_set() or self._failure:return TickResult(stopped=True)
                self._active+=1
            try:
                self._identity()
                def step():
                    self._identity();handles=self.runner.handles();count=len(handles)
                    if not count:return ()
                    start=self._cursor%count;ordered=handles[start:]+handles[:start]
                    selected=ordered[:self.max_per_tick];self._cursor=(start+len(selected))%count
                    advanced=[]
                    for handle in selected:
                        if self._stop.is_set():break
                        self._identity()
                        if handle.token.cancelled or handle.unresolved:continue
                        now=self.clock()
                        if isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now) or now<0:raise ValueError('Invalid pump clock')
                        self.runner.advance(handle.task_id,handle.token,now)
                        self._identity();advanced.append(handle.task_id)
                    return tuple(advanced)
                result=self.host.advance_owned(step)
                self._identity()
                if result.busy:return TickResult(busy=True)
                # The hosted work reservation has fully returned; settlement
                # will still report busy if another lifecycle mutation began.
                settlement=None if self._stop.is_set() else self.host.poll()
                self._identity()
                return TickResult(advanced=result.value,settlement=settlement)
            except BaseException:
                with self._condition:self._failure='engine_pump_unresolved';self._stop.set()
                raise
            finally:
                with self._condition:self._active-=1;self._condition.notify_all()
        finally:self._tick_lock.release()
    def _run(self):
        try:
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(self.interval)
        except BaseException:
            # Failure recorded by tick; never retry a possibly effectful step.
            pass
        finally:
            with self._condition:self._condition.notify_all()
    def start(self):
        with self._condition:
            self._identity()
            if self._failure:raise RuntimeUnavailable('Pump requires reconciliation')
            if self._stoppers:raise RuntimeUnavailable('Pump stop is in progress')
            if self._thread is not None and self._thread.is_alive():return False
            if self._active:raise RuntimeUnavailable('Prior pump callback has not drained')
            self._stop.clear()
            thread=Thread(target=self._run,name='artex-engine-pump',daemon=False)
            self._thread=thread
            try:thread.start()
            except BaseException:
                self._stop.set()
                if thread.ident is None:self._thread=None
                raise
            return True
    def stop(self,timeout):
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<0:raise ValueError('Finite nonnegative stop timeout required')
        deadline=monotonic()+timeout
        with self._condition:
            self._stoppers+=1;self._stop.set();thread=self._thread
        try:
            if thread is current_thread():return False
            if thread is not None:thread.join(max(0,deadline-monotonic()))
            with self._condition:
                self._condition.wait_for(lambda:not self._active,max(0,deadline-monotonic()))
                return not self._active and (thread is None or not thread.is_alive())
        finally:
            with self._condition:self._stoppers-=1;self._condition.notify_all()
