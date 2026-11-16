"""Opt-in exact-task metered by-model summary; no debug capture or billing data."""
from urllib.parse import parse_qsl,urlsplit
from .core import json_response,error_response
from .disclosure import DisclosureResponse
from ..db.core import Query
from ..db.llm_records import LLMRecordStore,RecordUnavailable

class TaskModelUsageRead:
    def __init__(self,executor,scope,*,authorize,request_authorize,current,lifecycle_guard,max_rows=500,max_bytes=1048576):
        from .task_runtime_reads import TaskReadScope
        if type(scope) is not TaskReadScope:raise ValueError('Exact selected task scope required')
        if any(not callable(f) for f in (authorize,request_authorize,current,lifecycle_guard)):
            raise ValueError('Independent model usage authority required')
        if type(max_rows) is not int or not 1<=max_rows<=10000 or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:
            raise ValueError('Explicit model summary bounds required')
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
            pure();transport_disclosure()
            with guard(s):check()
            check();authority();transport_authentication();pure()
        try:
            if not callable(transport_disclosure) or not callable(transport_authentication):raise ValueError()
            if request.method.upper() not in ('GET','HEAD') or request.body:raise ValueError()
            pairs=parse_qsl(urlsplit(request.target).query,keep_blank_values=True,strict_parsing=True,max_num_fields=1)
            if pairs!=[('task',str(s.task_id))]:raise ValueError()
            receipt()
            with guard(s):
                check();authority()
                with e.transaction() as tx:
                    parent=tx.fetch_all(Query('''SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2
AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE''',self._identity[:2]))
                    if parent!=[(s.task_id,)]:return error_response(404,'Model usage task unavailable')
                    check();authority()
                    output=LLMRecordStore(tx,task_id=str(s.task_id)).token_by_model(str(s.task_id),max_rows=rows,max_bytes=maximum)
                    check();authority()
                    response=json_response(200,{'models':output})
                    if len(response.body)>maximum:raise RecordUnavailable('Summary bound')
            receipt()
            return DisclosureResponse(response.status,response.body,response.headers,receipt)
        except PermissionError:return error_response(403,'Model usage access denied')
        except (UnicodeError,ValueError):return error_response(400,'Invalid model usage request')
        except Exception:return error_response(503,'Model usage unavailable')
