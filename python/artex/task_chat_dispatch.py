"""Task-main dispatch with explicit scope fences and task activity persistence.

Reuses the bounded keyed scheduler from chat_dispatch, NOT conversation storage.
Its internal receipt key uses task_id; an explicit projection stamps mainagent and
main_seg onto every task activity and ignores conversation-only title updates.
The supplied task sink must expose task_id and transactional append_many(items).
No manager.start/resume, task goal/hint mutation, rule fallback or inferred roots.
Host deletion/archive/clear must share core.guard and cancel/drain this dispatcher.
A changed scope fails publication and retains unresolved ownership for reconciliation.
The scheduler's single-process, receipt-bound and crash-recovery limits still apply.
"""
from copy import deepcopy
from dataclasses import asdict,dataclass
import hashlib,json,math,time
from threading import Condition,RLock,get_ident
from contextlib import contextmanager
from .chat_dispatch import ChatDispatcher,DispatchUnavailable
from .task_chat import TaskChatCore,TaskChatScope,scope_identity


class _TaskActivityProjection:
    def __init__(self,core,scope,sink,ticket):
        self.core,self.scope,self.sink,self.ticket=core,scope,sink,ticket
        # Private keyed-scheduler adapter, not a ConversationActivityStore.
        self.conversation_id=scope.task_id
    def append_many_and_touch(self,items,title_if_default=None):
        with self.core._project_queued(self.ticket),self.core.guard(self.scope):
            self.core._check(self.scope,self.scope.session_id)
            if (getattr(self.sink,'task_id',None)!=self.scope.task_id
                or getattr(self.sink,'exploration_id',None)!=self.scope.exploration_id
                or getattr(self.sink,'scope_identity',None)!=scope_identity(self.scope)):
                raise DispatchUnavailable('Task activity scope changed')
            records=deepcopy(items)
            if self.ticket.authorize is not None and self.ticket.authorize() is not True:
                raise PermissionError("Task request denied")
            for item in records:item.worker='mainagent';item.main_seg=self.scope.segment
            return self.sink.append_many(records)


@dataclass(frozen=True)
class TaskDispatchDrain:
    drained:bool
    alive_workers:int
    unresolved:int
    failed_executions:int
    outer_invocations:int
    core_invocations:int

class TaskChatDispatcher:
    def __init__(self,core,activity_store,*,workers=1,capacity=4,max_receipts=10000,single_process=False):
        if not isinstance(core,TaskChatCore) or not callable(activity_store):
            raise ValueError('Explicit task core and activity store required')
        self.core=self._core=core;self.activity_store=activity_store
        self._lifetime=Condition(RLock());self._closing=False;self._outer={}
        self._queue=ChatDispatcher(workers=workers,capacity=capacity,max_receipts=max_receipts,single_process=single_process)
    def _check_core(self):
        if self.core is not self._core:raise DispatchUnavailable('Original task core required')
    @contextmanager
    def _invocation(self):
        self._check_core()
        receipt=object()
        with self._lifetime:
            if self._closing:raise DispatchUnavailable('Task dispatcher admission sealed')
            self._outer[receipt]=get_ident()
        try:yield
        finally:
            with self._lifetime:self._outer.pop(receipt,None);self._lifetime.notify_all()
    @contextmanager
    def _cancellation(self):
        # Cancellation remains callable after seal, but its callbacks are owners.
        receipt=object()
        with self._lifetime:self._outer[receipt]=get_ident()
        try:yield
        finally:
            with self._lifetime:self._outer.pop(receipt,None);self._lifetime.notify_all()
    def prepare(self,task_id,segment=None):
        with self._invocation():return self._core.prepare(task_id,segment=segment)
    def submit(self,scope,client_id,message,*,authorize=None):
        with self._invocation():
            if authorize is not None and not callable(authorize):raise ValueError("Explicit admission check required")
            ticket=self._core._reserve_queued(scope,authorize)
            try:
                receipt=self._submit(scope,client_id,message,ticket)
                receipt.future.add_done_callback(lambda f,core=self._core:core._leave(ticket))
                return receipt
            except BaseException:
                self._core._leave(ticket);raise
    def _submit(self,scope,client_id,message,ticket):
        if not isinstance(scope,TaskChatScope) or scope!=ticket.scope:raise ValueError('Original prepared task scope required')
        sink=self.activity_store(scope)
        if (getattr(sink,'task_id',None)!=scope.task_id or getattr(sink,'exploration_id',None)!=scope.exploration_id
            or getattr(sink,'scope_identity',None)!=scope_identity(scope)
            or not callable(getattr(sink,'append_many',None))):
            raise DispatchUnavailable('Transactional task-scoped activity sink required')
        if not all(callable(getattr(self._core.transcript,name,None)) for name in ('begin_scoped','check_scoped')):
            raise DispatchUnavailable('Durable scoped transcript admission required')
        projection=_TaskActivityProjection(self._core,scope,sink,ticket)
        # Same client ID cannot silently reuse an admission for a different
        # segment/run/revision/model or prompt, even when its text is unchanged.
        identity=hashlib.sha256(json.dumps(asdict(scope),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        def run(token):return self._core._run_queued(ticket,client_id,message,token)
        with self._core.guard(scope):
            self._core._check(scope,scope.session_id)
            self._core.transcript.check_scoped(scope.session_id,client_id,
                {'role':'user','content':[{'type':'text','text':message}]},scope_identity(scope))
            if ticket.authorize is not None and ticket.authorize() is not True:
                raise PermissionError("Task request denied")
            return self._queue.submit(scope.task_id,client_id,message,activities=projection,run=run,worker='mainagent:'+identity)
    def running(self,task_id):
        self._check_core();return self._queue.running(task_id) or self._core.running(task_id)
    def cancel(self,task_id):
        self._check_core()
        with self._cancellation():
            queued=self._queue.cancel(task_id);active=self._core.cancel(task_id)
            return queued or active
    def close(self,*,cancel=False,timeout=None):
        self._check_core()
        if type(cancel) is not bool or timeout is not None and (type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<0):raise ValueError('Invalid task drain options')
        deadline=None if timeout is None else time.monotonic()+timeout
        remaining=lambda:None if deadline is None else max(0,deadline-time.monotonic())
        with self._lifetime:self._closing=True
        self._core.seal(cancel=cancel)
        queue_worker=any(t.ident==get_ident() for t in self._queue._threads)
        with self._lifetime:
            while self._outer:
                if queue_worker or get_ident() in self._outer.values() or remaining()==0:break
                self._lifetime.wait(remaining())
            outer=len(self._outer)
        if outer or queue_worker:
            core=self._core.snapshot()
            with self._queue._condition:
                alive=sum(t.is_alive() for t in self._queue._threads)
                unresolved=sum(j.uncertain for j in self._queue._busy.values())
                failures=self._queue._failures
            return TaskDispatchDrain(False,alive,unresolved+core.unresolved,failures,outer,core.active_invocations)
        with self._cancellation():
            queue=self._queue.close(cancel=cancel,timeout=remaining())
        core=self._core.wait(remaining())
        with self._lifetime:outer=len(self._outer)
        return TaskDispatchDrain(queue.drained and core.drained and not outer,queue.alive_workers,
            queue.unresolved+core.unresolved,queue.failed_executions,outer,core.active_invocations)
