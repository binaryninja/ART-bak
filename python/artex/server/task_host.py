"""Opt-in TaskService facade over the exact AgentHost lifecycle fence.

No factory default or route wiring changes. All execution admission requires an
explicit admission_guard(operation, task_id) context manager yielding literal
True. It must reserve capacity using the SAME authority as queued admissions,
hold that reservation until hosted mutation finishes, and reconcile uncertainty.
It is not a readiness predicate: a no-op allow-all adapter alongside a capacity
scheduler violates this contract. Missing policies fail before persistence.
The current FIFO scheduler supplies no such direct-admission adapter; direct
HTTP start/create/resume remain unavailable unless the host supplies one.

Read APIs and only name/pinned metadata delegate to the manager. Pause goes
through the host but does not consume admission capacity. No broad delegation,
queue dequeue, scheduler replacement, completion, deletion or category mutation.
Stored metadata is not runtime success. Host poll/drain remains explicit.
"""
from ..agent.host import AgentHost
from ..manager import RuntimeUnavailable

class TaskHTTPManagerFacade:
    def __init__(self,host,*,admission_guard=None):
        if not isinstance(host,AgentHost) or host.manager.runner is not host.runner:
            raise RuntimeUnavailable('Exact hosted task manager required')
        if admission_guard is not None and not callable(admission_guard):raise ValueError('Admission guard must be callable')
        self.host=host;self._manager=host.manager;self._admission=admission_guard
    def _identity(self):
        if self.host.manager is not self._manager or self._manager.runner is not self.host.runner:
            raise RuntimeUnavailable('Hosted runner identity changed')
    def _admit(self,operation,task_id,callback):
        self._identity()
        if self._admission is None:raise RuntimeUnavailable('Shared capacity admission policy is unavailable')
        with self._admission(operation,task_id) as accepted:
            if accepted is not True:raise RuntimeUnavailable('Capacity admission was not granted')
            self._identity()
            return callback()
    def list_tasks(self):self._identity();return self._manager.list_tasks()
    def task(self,task_id):self._identity();return self._manager.task(task_id)
    def active_task_id(self):self._identity();return self._manager.active_task_id()
    def create_task(self,description,goal,options=None,*,start=True,seed_first_intent=False):
        if type(start) is not bool or type(seed_first_intent) is not bool:raise ValueError('Boolean admission flags required')
        call=lambda:self.host.create_task(description,goal,options,start=start,seed_first_intent=seed_first_intent)
        # Even metadata-only creation consumes catalog resources: keep the same
        # policy boundary instead of inventing a bypass for an alternate caller.
        return self._admit('create',None,call)
    def start(self,task_id,*,seed_first_intent=False):
        if type(seed_first_intent) is not bool:raise ValueError('Boolean seeding flag required')
        return self._admit('start',task_id,lambda:self.host.start(task_id,seed_first_intent=seed_first_intent))
    def control_task(self,task_id,action):
        if action=='pause':self._identity();return self.host.control_task(task_id,action)
        if action!='resume':raise ValueError('action must be pause|resume')
        return self._admit('resume',task_id,lambda:self.host.control_task(task_id,action))
    def update_metadata(self,task_id,**patch):
        self._identity()
        if not patch or set(patch)-{'name','pinned'}:raise ValueError('Only name/pinned metadata is supported')
        return self.host._invoke(lambda:self._manager.update_metadata(task_id,**patch),admission=False)
