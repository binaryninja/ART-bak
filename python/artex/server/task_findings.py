"""Explicit task branch of findings GET; no global or mutation delegation."""
from urllib.parse import parse_qsl,urlsplit
from .core import json_response,error_response
from .task_goals import _id
from ..db.task_findings import TaskFindingsStore
from ..db.task_coverage import CoverageDenied,CoverageUnavailable
class TaskFindingsService:
    def __init__(self,store,*,authorize):
        if type(store) is not TaskFindingsStore or not callable(authorize):raise ValueError('Actual task findings store and request grant required')
        self.store=store;self.authorize=authorize;self._store=store
    def register(self,router):router.add('GET','/api/exploration/findings',lambda r,p:self.handle(r))
    def handle(self,request):
        try:
            if request.body:raise ValueError()
            query=urlsplit(request.target).query
            if len(query)>128:raise ValueError()
            pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,max_num_fields=2)
            if len(pairs)!=1 or pairs[0][0]!='task' or _id(pairs[0][1])!=self._store.scope.task_id:raise ValueError()
            grant=self.authorize;store=self._store
            def allowed():
                if self.store is not store or self.authorize is not grant:raise CoverageDenied('Finding request policy changed')
                store._check()
                if grant(request,store.scope) is not True:raise CoverageDenied('Finding read denied')
                if self.store is not store or self.authorize is not grant:raise CoverageDenied('Finding request policy changed')
                store._check();return True
            result,validate=store.read(allowed)
            response=json_response(200,result)
            if len(response.body)>store.coverage.max_bytes:raise CoverageUnavailable('Finding response bound exceeded')
            validate();return response
        except CoverageDenied:return error_response(403,'Task finding read denied')
        except CoverageUnavailable:return error_response(503,'Task findings unavailable')
        except (ValueError,KeyError,UnicodeError,TypeError):return error_response(400,'Invalid task finding request')
        except Exception:return error_response(503,'Task findings unavailable')
