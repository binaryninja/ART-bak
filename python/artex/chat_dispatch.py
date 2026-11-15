"""Bounded, explicitly constructed conversation background dispatcher.

Requires a single-process authority for each conversation. All admission/stop and
lifetime/deletion paths must share this authority. Local client receipts are
bounded and retained (never evicted for silent duplicate execution). JSONL's
ChatTranscriptAdapter supplies durable turn idempotency, NOT an atomic activity/
transcript transaction: restart may duplicate user activity. No distributed lease,
crash replay, automatic recovery, retest settlement, attachment
handling or incremental tool activity projection is claimed. Caller must reject
special retest agents until their settlement lifecycle is implemented.

User activity is durable before acceptance; cancellation is registered before
that write and cannot be lost while queued. Terminal activity is emitted only
from an explicitly persisted ChatOutcome. Failed/uncertain activity persistence
retains busy ownership for operator reconciliation; close reports it, not success.
Callbacks already blocked in I/O cannot be preempted. Explicit construction starts
non-daemon threads; host must close/drain them. No provider/tool defaults exist.
"""
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass,replace
from threading import Condition,Event,RLock,Thread,current_thread
import re,time,uuid,math
from .agent.chat import ChatOutcome
from .agent.runtime import RunStopped
from .db.conversation_activity import Activity
from .db.core import int64

class DispatchUnavailable(RuntimeError):pass
class DispatchConflict(RuntimeError):pass
class DispatchPersistenceError(RuntimeError):pass
class DispatchExecutionError(RuntimeError):pass

class Cancellation:
    def __init__(self):self._event=Event();self._lock=RLock();self._callbacks=[]
    @property
    def cancelled(self):return self._event.is_set()
    def check(self):
        if self.cancelled:raise RunStopped('cancelled')
    def on_cancel(self,callback):
        if not callable(callback):raise ValueError('Cancellation callback required')
        with self._lock:
            now=self.cancelled
            if not now:self._callbacks.append(callback)
        if now:callback()
    def cancel(self):
        with self._lock:
            self._event.set();callbacks=self._callbacks;self._callbacks=[]
        for callback in callbacks:
            try:callback()
            except BaseException:pass  # token remains set; no secret-bearing callback errors logged

@dataclass(frozen=True)
class Receipt:
    id:str
    conversation_id:int
    client_id:str
    future:Future
    reused:bool=False

@dataclass(frozen=True)
class DrainResult:
    drained:bool
    alive_workers:int
    unresolved:int
    failed_executions:int

@dataclass
class _Job:
    receipt:Receipt
    message:str
    activities:object
    run:object
    worker:str
    token:Cancellation
    uncertain:bool=False

class ChatDispatcher:
    def __init__(self,*,workers=1,capacity=4,max_receipts=10000,single_process=False):
        if single_process is not True:raise ValueError('Explicit single-process conversation authority required')
        if any(type(v) is not int or v<=0 for v in (workers,capacity,max_receipts)):raise ValueError('Positive dispatcher bounds required')
        if workers>64 or capacity>10000 or max_receipts>1000000:raise ValueError('Dispatcher bounds too large')
        self._limit=workers+capacity;self._max_receipts=max_receipts
        self._condition=Condition(RLock());self._queue=deque();self._busy={};self._receipts={};self._closed=False;self._failures=0
        self._threads=[Thread(target=self._worker,name='artex-chat',daemon=False) for _ in range(workers)]
        for thread in self._threads:thread.start()

    def submit(self,conversation_id,client_id,message,*,activities,run,worker=''):
        int64(conversation_id,'conversation ID')
        if conversation_id<=0:raise ValueError('Positive conversation ID required')
        if not isinstance(client_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',client_id):raise ValueError('Invalid client ID')
        if not isinstance(message,str) or not message.strip() or '\x00' in message or len(message.encode('utf-8'))>65536:raise ValueError('Invalid message')
        if not isinstance(worker,str) or len(worker)>120 or '\x00' in worker:raise ValueError('Invalid worker')
        if getattr(activities,'conversation_id',None)!=conversation_id or not callable(getattr(activities,'append_many_and_touch',None)) or not callable(run):raise ValueError('Explicit parent-scoped activity store and runner required')
        key=(conversation_id,client_id)
        with self._condition:
            previous=self._receipts.get(key)
            if previous is not None:
                if previous.message!=message or previous.worker!=worker:raise DispatchConflict('Client ID reused with different content')
                if previous.uncertain:raise DispatchPersistenceError('Previous admission persistence unconfirmed')
                return replace(previous.receipt,reused=True)
            if self._closed:raise DispatchUnavailable('Dispatcher closed')
            if conversation_id in self._busy:raise DispatchConflict('Conversation already running or requires reconciliation')
            if len(self._busy)>=self._limit or len(self._receipts)>=self._max_receipts:raise DispatchUnavailable('Dispatcher capacity exhausted')
            future=Future();future.set_running_or_notify_cancel()
            receipt=Receipt(uuid.uuid4().hex,conversation_id,client_id,future)
            job=_Job(receipt,message,activities,run,worker,Cancellation())
            # In-progress admission cannot be reused as successful before durability.
            job.uncertain=True
            self._busy[conversation_id]=job;self._receipts[key]=job
        try:
            ids=activities.append_many_and_touch([Activity(worker=worker,kind='user',summary=message.split('\n',1)[0][:200],detail=message)],title_if_default=message.strip().split('\n',1)[0][:40])
            if not isinstance(ids,list) or len(ids)!=1 or type(ids[0]) is not int or ids[0]<=0:raise DispatchPersistenceError()
        except BaseException:
            with self._condition:
                self._failures+=1
                future.set_exception(DispatchPersistenceError('User activity persistence unconfirmed'))
                self._condition.notify_all()
            raise DispatchPersistenceError('User activity persistence unconfirmed') from None
        with self._condition:
            job.uncertain=False;self._queue.append(job);self._condition.notify_all()
        return receipt

    def running(self,conversation_id):
        int64(conversation_id,'conversation ID')
        with self._condition:return conversation_id in self._busy

    def cancel(self,conversation_id):
        int64(conversation_id,'conversation ID')
        with self._condition:job=self._busy.get(conversation_id)
        if job is None:return False
        job.token.cancel();return True

    def _worker(self):
        while True:
            with self._condition:
                while not self._queue:
                    # An admission may still be persisting while close begins.
                    pending=any(job.uncertain and not job.receipt.future.done() for job in self._busy.values())
                    if self._closed and not pending:return
                    self._condition.wait()
                job=self._queue.popleft()
            outcome=None;error=None
            try:
                if job.token.cancelled:
                    activity=Activity(worker=job.worker,kind='result',summary='Cancelled before execution',detail='Cancelled before execution',is_error=True)
                else:
                    outcome=job.run(job.token)
                    if not isinstance(outcome,ChatOutcome) or not outcome.persisted or outcome.stale or outcome.result is None:
                        raise DispatchExecutionError('Chat result persistence not confirmed')
                    result=outcome.result
                    usage=result.usage
                    activity=Activity(worker=job.worker,kind='result',summary=result.text[:200],detail=result.text,
                                      is_error=result.reason not in ('complete','completed','end_turn','stop'))
                    for name in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'):
                        if name in usage:setattr(activity,name,usage[name])
            except BaseException:
                job.uncertain=True  # transcript/provider effects may require reconciliation
                error=DispatchExecutionError('Conversation execution failed; inspect durable state before retrying')
                activity=Activity(worker=job.worker,kind='text',is_error=True,summary='Conversation execution failed',detail='Conversation execution failed')
            try:
                ids=job.activities.append_many_and_touch([activity])
                if not isinstance(ids,list) or len(ids)!=1 or type(ids[0]) is not int or ids[0]<=0:raise DispatchPersistenceError()
            except BaseException:
                error=DispatchPersistenceError('Terminal activity persistence unconfirmed')
                job.uncertain=True
            with self._condition:
                if not job.uncertain:self._busy.pop(job.receipt.conversation_id,None)
                if error is not None:self._failures+=1
                try:
                    if error is not None:job.receipt.future.set_exception(error)
                    else:job.receipt.future.set_result(outcome)
                except BaseException:
                    pass  # Future callbacks cannot kill the owned worker after settlement.
                self._condition.notify_all()

    def close(self,*,cancel=False,timeout=None):
        if timeout is not None and (not isinstance(timeout,(int,float)) or isinstance(timeout,bool) or not math.isfinite(timeout) or timeout<0):raise ValueError('Invalid drain timeout')
        if current_thread() in self._threads:raise DispatchUnavailable('Worker cannot join its own dispatcher')
        deadline=None if timeout is None else time.monotonic()+timeout
        with self._condition:
            self._closed=True;jobs=list(self._busy.values());self._condition.notify_all()
        if cancel:
            for job in jobs:job.token.cancel()
        for thread in self._threads:thread.join(None if deadline is None else max(0,deadline-time.monotonic()))
        with self._condition:
            alive=sum(t.is_alive() for t in self._threads);unresolved=sum(j.uncertain for j in self._busy.values())
            return DrainResult(not alive and not self._busy,alive,unresolved,self._failures)
