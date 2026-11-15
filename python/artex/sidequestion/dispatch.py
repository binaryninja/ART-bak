"""Explicit bounded worker dispatch. Construction starts non-daemon threads.

No module-global runtime, providers or workers exist. dispatch returns True only
for accepted execution ownership, NOT completed inference. Inspect receipt(handle)
or failures() for eventual persistence errors. Duplicate accepted handles share a
receipt and are never queued twice (recent receipt cache is bounded to 128).

Full/closed rejection aborts an unstarted admission durably before SideUnavailable.
If abort persistence fails, SidePersistenceError exposes .handle and ownership is
retained in the coordinator and dispatcher; retry_rejections() can settle it.
Any worker failure stops new dispatches and is retained for the host to inspect.
close(cancel=True) requests cooperative cancellation; a blocked provider is not
preempted. Its result explicitly reports live threads and unsettled persistence.
All access to a database executor must be thread-safe (PostgresExecutor serializes
its connection). The host owns restart recovery and operational error reporting.
"""
from collections import OrderedDict
from concurrent.futures import Future,InvalidStateError
from dataclasses import dataclass
from queue import Queue,Empty,Full
from threading import RLock,Thread,current_thread
import time
import math
from .runtime import Admission,SideUnavailable,SidePersistenceError
from ..db.sidequestions import SideInvalid


@dataclass(frozen=True)
class DrainResult:
    drained: bool
    alive_workers: int
    unresolved_rejections: int
    failed_executions: int


class SideQuestionDispatcher:
    def __init__(self,coordinator,*,workers=1,capacity=4):
        if type(workers) is not int or not 1<=workers<=4:raise SideInvalid('workers must be between 1 and 4')
        if type(capacity) is not int or not 1<=capacity<=4:raise SideInvalid('queue capacity must be between 1 and 4')
        self.coordinator=coordinator;self._queue=Queue(maxsize=capacity);self._lock=RLock()
        self._closed=False;self._faulted=False;self._active={};self._receipts=OrderedDict()
        self._rejected={};self._failures={};self._threads=[];self._identities={}
        try:
            for index in range(workers):
                thread=Thread(target=self._worker,name=f'artex-side-{index}',daemon=False)
                thread.start();self._threads.append(thread)
        except BaseException:
            self._closed=True
            for thread in self._threads:thread.join()
            raise

    def _reject(self,admission):
        handle=admission.handle
        try:self.coordinator.abort_unstarted(handle,dispatch_owner=self)
        except SidePersistenceError as exc:
            self._rejected[handle]=admission
            self._faulted=True
            exc.handle=handle
            raise
        except SideInvalid:
            raise SideUnavailable('Admission is absent or already owned by another executor') from None
        raise SideUnavailable('Side dispatch is closed, full, or requires recovery')

    def dispatch(self,admission):
        if not isinstance(admission,Admission) or not admission.created or admission.handle is None:
            raise SideInvalid('dispatch requires a newly created admission')
        with self._lock:
            handle=admission.handle
            identity=(admission.exchange.id,admission.exchange.session_key,admission.exchange.generation)
            if handle in self._receipts:
                if self._identities[handle]!=identity:raise SideInvalid('admission does not match its receipt')
                return True
            self.coordinator.claim_dispatch(admission,self)
            if self._closed or self._faulted:return self._reject(admission)
            future=Future()
            self._active[handle]=future
            try:self._queue.put_nowait((handle,future))
            except Full:
                self._active.pop(handle,None)
                return self._reject(admission)
            self._receipts[handle]=future
            self._identities[handle]=identity
            # Do not evict active receipts; at most four coordinator admissions.
            while len(self._receipts)>128:
                oldest=next(iter(self._receipts))
                if oldest in self._active:self._receipts.move_to_end(oldest);continue
                self._receipts.pop(oldest)
                self._identities.pop(oldest,None)
            return True

    def receipt(self,handle):
        with self._lock:return self._receipts.get(handle)

    def failures(self):
        """Outstanding execution exceptions; no exception strings are logged."""
        with self._lock:return dict(self._failures)

    def retry_rejections(self):
        """Retry durable aborts; does not reopen a faulted dispatcher."""
        with self._lock:
            for handle in tuple(self._rejected):
                try:self.coordinator.abort_unstarted(handle,dispatch_owner=self)
                except SidePersistenceError:continue
                except SideInvalid:continue
                else:self._rejected.pop(handle,None)
            return len(self._rejected)

    def _worker(self):
        while True:
            try:handle,future=self._queue.get(timeout=0.05)
            except Empty:
                with self._lock:
                    if self._closed and self._queue.empty():return
                continue
            try:
                # Host cancellation of the Future must not discard a durable row.
                try:outcome=self.coordinator.execute(handle,dispatch_owner=self)
                except BaseException as exc:
                    with self._lock:
                        self._failures[handle]=exc;self._faulted=True
                    try:future.set_exception(exc)
                    except InvalidStateError:pass
                else:
                    try:future.set_result(outcome)
                    except InvalidStateError:pass
            finally:
                with self._lock:self._active.pop(handle,None)
                self._queue.task_done()

    def close(self,*,cancel=False,timeout=None):
        if timeout is not None and (type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<0):raise SideInvalid('invalid drain timeout')
        deadline=None if timeout is None else time.monotonic()+timeout
        with self._lock:
            self._closed=True
            if cancel:
                for handle in self._active:self.coordinator.cancel(handle)
        for thread in self._threads:
            if thread is current_thread():continue
            remaining=None if deadline is None else max(0,deadline-time.monotonic())
            thread.join(remaining)
        with self._lock:
            alive=sum(thread.is_alive() for thread in self._threads)
            return DrainResult(alive==0,alive,len(self._rejected),len(self._failures))
