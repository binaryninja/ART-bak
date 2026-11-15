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
from dataclasses import asdict
import hashlib,json
from .chat_dispatch import ChatDispatcher,DispatchUnavailable
from .task_chat import TaskChatCore,TaskChatScope,scope_identity


class _TaskActivityProjection:
    def __init__(self,core,scope,sink):
        self.core,self.scope,self.sink=core,scope,sink
        # Private keyed-scheduler adapter, not a ConversationActivityStore.
        self.conversation_id=scope.task_id
    def append_many_and_touch(self,items,title_if_default=None):
        with self.core.guard(self.scope):
            self.core._check(self.scope,self.scope.session_id)
            if (getattr(self.sink,'task_id',None)!=self.scope.task_id
                or getattr(self.sink,'exploration_id',None)!=self.scope.exploration_id
                or getattr(self.sink,'scope_identity',None)!=scope_identity(self.scope)):
                raise DispatchUnavailable('Task activity scope changed')
            records=deepcopy(items)
            for item in records:item.worker='mainagent';item.main_seg=self.scope.segment
            return self.sink.append_many(records)


class TaskChatDispatcher:
    def __init__(self,core,activity_store,*,workers=1,capacity=4,max_receipts=10000,single_process=False):
        if not isinstance(core,TaskChatCore) or not callable(activity_store):
            raise ValueError('Explicit task core and activity store required')
        self.core,self.activity_store=core,activity_store
        self._queue=ChatDispatcher(workers=workers,capacity=capacity,max_receipts=max_receipts,single_process=single_process)
    def prepare(self,task_id,segment=None):return self.core.prepare(task_id,segment=segment)
    def submit(self,scope,client_id,message):
        if not isinstance(scope,TaskChatScope):raise ValueError('Prepared task scope required')
        sink=self.activity_store(scope)
        if (getattr(sink,'task_id',None)!=scope.task_id or getattr(sink,'exploration_id',None)!=scope.exploration_id
            or getattr(sink,'scope_identity',None)!=scope_identity(scope)
            or not callable(getattr(sink,'append_many',None))):
            raise DispatchUnavailable('Transactional task-scoped activity sink required')
        if not all(callable(getattr(self.core.transcript,name,None)) for name in ('begin_scoped','check_scoped')):
            raise DispatchUnavailable('Durable scoped transcript admission required')
        projection=_TaskActivityProjection(self.core,scope,sink)
        # Same client ID cannot silently reuse an admission for a different
        # segment/run/revision/model or prompt, even when its text is unchanged.
        identity=hashlib.sha256(json.dumps(asdict(scope),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        def run(token):return self.core.run(scope.task_id,client_id,message,segment=scope.segment,scope=scope,token=token)
        with self.core.guard(scope):
            self.core._check(scope,scope.session_id)
            self.core.transcript.check_scoped(scope.session_id,client_id,
                {'role':'user','content':[{'type':'text','text':message}]},scope_identity(scope))
            return self._queue.submit(scope.task_id,client_id,message,activities=projection,run=run,worker='mainagent:'+identity)
    def running(self,task_id):return self._queue.running(task_id) or self.core.running(task_id)
    def cancel(self,task_id):
        queued=self._queue.cancel(task_id);active=self.core.cancel(task_id)
        return queued or active
    def close(self,*,cancel=False,timeout=None):return self._queue.close(cancel=cancel,timeout=timeout)
