"""Opt-in exact-task own-task scope metadata; no scope changes or testing authority."""
from urllib.parse import urlsplit
from contextlib import nullcontext
from .core import json_response,error_response
from .disclosure import DisclosureResponse
from ..db.core import Query
from ..db.task_scope import TaskScopeStore,ScopeDenied,ScopeUnavailable

class TaskScopeRead:
    def __init__(self,executor,scope,*,authorize,request_authorize,current,lifecycle_guard,max_rows=500,max_bytes=1048576):
        from .task_runtime_reads import TaskReadScope
        if type(scope) is not TaskReadScope:raise ValueError('Exact selected task scope required')
        if any(not callable(f) for f in (authorize,request_authorize,current,lifecycle_guard)):
            raise ValueError('Independent scope read authority required')
        if type(max_rows) is not int or not 1<=max_rows<=10000 or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:
            raise ValueError('Explicit scope response bounds required')
        self._pins=(executor,scope,authorize,request_authorize,current,lifecycle_guard,max_rows,max_bytes)
        self._original=self._pins
        self._identity=(scope.task_id,scope.exploration_id,scope.revision)
    def handle(self,request,*,transport_disclosure,transport_authentication):
        e,s,source,http,current,guard,rows,maximum=self._original
        def pure():
            if self._pins is not self._original or (s.task_id,s.exploration_id,s.revision)!=self._identity:
                raise PermissionError()
        def check():
            pure()
            if current(s) is not True:raise PermissionError()
            pure()
        def authority():
            pure()
            if http(request,s) is not True:raise PermissionError()
            pure()
            if source(s) is not True:raise PermissionError()
            pure()
        def receipt():
            pure();authority();transport_disclosure()
            with guard(s):check()
            check();authority()
            if e.fetch_all(Query('SELECT 1 FROM task_relations WHERE task_id=$1 LIMIT 1',(s.task_id,))):
                raise ScopeUnavailable('Inherited scope unavailable')
            authority();transport_authentication();pure()
        try:
            if not callable(transport_disclosure) or not callable(transport_authentication):raise ValueError()
            if request.method.upper() not in ('GET','HEAD') or request.body:raise ValueError()
            if urlsplit(request.target).query or request.path!=f'/api/tasks/{s.task_id}/scope':raise ValueError()
            receipt()
            with guard(s):
                check();authority()
                with e.transaction() as tx:
                    parent=tx.fetch_all(Query('''SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2
AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE''',self._identity[:2]))
                    if parent!=[(s.task_id,)]:return error_response(404,'Scope read task unavailable')
                    check();authority()
                    output=TaskScopeStore(tx,s.task_id,lifecycle_guard=nullcontext,current=lambda:check() is None,
                        approve=lambda entry:False,expected_exploration_id=s.exploration_id,
                        read_authorize=lambda task,exp:authority() is None).list_bounded()
                    if len(output)>rows:raise ScopeUnavailable('Scope row bound')
                    check();authority()
                    response=json_response(200,{'scope':output})
                    if len(response.body)>maximum:raise ScopeUnavailable('Summary bound')
            receipt()
            return DisclosureResponse(response.status,response.body,response.headers,receipt)
        except (PermissionError,ScopeDenied):return error_response(403,'Scope read access denied')
        except (UnicodeError,ValueError):return error_response(400,'Invalid scope read request')
        except Exception:return error_response(503,'Scope read unavailable')
