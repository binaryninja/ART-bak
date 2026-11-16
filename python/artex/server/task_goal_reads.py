"""Bounded read-only goal projection for an already resolved task scope.

No goal CRUD handlers, goal state transitions, automatic completion or implicit
read grants. Uses the actual exploration goal projection and source goal DTO.
"""
from .core import json_response,error_response
from .task_runtime_reads import TaskReadScope
from .task_goals import _dto
from ..db.core import Query
from ..db.exploration import ExplorationStore,GraphViewOverflow

class TaskGoalReads:
    def __init__(self,executor,scope,*,authorize,request_authorize,current,lifecycle_guard):
        if type(scope) is not TaskReadScope or any(type(v) is not int or not 0<v<2**63 for v in (scope.task_id,scope.exploration_id)) or not isinstance(scope.revision,str) or not scope.revision:
            raise ValueError('Explicit resolved task scope required')
        if any(not callable(v) for v in (authorize,request_authorize,current,lifecycle_guard)):
            raise ValueError('Independent goal read grants required')
        self._pins=(executor,scope,authorize,request_authorize,current,lifecycle_guard)
        self._original_pins=self._pins
        self._identity=(scope.task_id,scope.exploration_id,scope.revision)
    def handle(self,request):
        e,s,source,http,current,guard=self._original_pins
        def check():
            if self._pins is not self._original_pins:raise PermissionError()
            if (s.task_id,s.exploration_id,s.revision)!=self._identity:raise PermissionError()
            if current(s) is not True:raise PermissionError()
            if (s.task_id,s.exploration_id,s.revision)!=self._identity:raise PermissionError()
        def allow():
            check()
            if http(request,s) is not True:raise PermissionError()
            check()
            if source(s) is not True:raise PermissionError()
            check()
        try:
            if request.method.upper() not in ('GET','HEAD') or request.body or request.target!=f'/api/tasks/{s.task_id}/goals':
                return error_response(400,'Invalid goal read selector')
            with guard(s):
                allow()
                with e.transaction() as tx:
                    rows=tx.fetch_all(Query('''SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2
AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE''',self._identity[:2]))
                    allow()
                    if rows!=[(s.task_id,)]:return error_response(404,'Local task goals unavailable')
                    store=ExplorationStore(tx,s.exploration_id)
                    output={'goals':[_dto(node) for node in store.list_goals_bounded()]}
                    allow()
                    response=json_response(200,output)
                    if len(response.body)>1048576:raise GraphViewOverflow()
                    allow()
                allow()
            allow()
            return response
        except PermissionError:return error_response(403,'Goal read access denied')
        except Exception:return error_response(503,'Goal read capability unavailable')
