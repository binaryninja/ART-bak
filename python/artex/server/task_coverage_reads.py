"""Explicit dynamic coverage disclosure; asset visibility is not test permission."""
from dataclasses import dataclass
from urllib.parse import urlsplit,parse_qsl
from .core import error_response
from .task_goals import _id
from .task_coverage import TaskCoverageService
from .task_runtime_reads import TaskReadScope
from ..db.task_coverage import TaskCoverageStore,CoverageScope,CoverageDenied

@dataclass(frozen=True)
class CoverageSelection:
    action:str
    asset_id:int|None=None

class TaskCoverageReads:
    def __init__(self,executor,scope,*,authorize,request_authorize,asset_authorize,current,lifecycle_guard,
                 connector_authorize=None,max_rows=500,max_bytes=1048576):
        if type(scope) is not TaskReadScope or any(type(v) is not int or not 0<v<2**63 for v in (scope.task_id,scope.exploration_id)):
            raise ValueError('Resolved scope required')
        if any(not callable(v) for v in (authorize,request_authorize,asset_authorize,current,lifecycle_guard)) or connector_authorize is not None and not callable(connector_authorize):raise ValueError('Independent coverage grants required')
        if type(max_rows) is not int or not 1<=max_rows<=500 or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Bounded coverage required')
        self._pins=(executor,scope,authorize,request_authorize,asset_authorize,current,lifecycle_guard,connector_authorize,max_rows,max_bytes)
        self._original=self._pins;self._identity=(scope.task_id,scope.exploration_id,scope.revision)
    def handle(self,action,request):
        e,s,source,http,asset,current,guard,connector,rows,bytes_=self._original
        def check():
            if self._pins is not self._original or (s.task_id,s.exploration_id,s.revision)!=self._identity:raise CoverageDenied()
            if current(s) is not True:raise CoverageDenied()
            if self._pins is not self._original or (s.task_id,s.exploration_id,s.revision)!=self._identity:raise CoverageDenied()
        try:
            suffix={'coverage':'coverage','graph':'coverage-graph','refs':'asset-refs'}.get(action)
            target=urlsplit(request.target)
            if suffix is None or request.method.upper() not in ('GET','HEAD') or request.body or target.path!=f'/api/tasks/{s.task_id}/{suffix}' or len(target.query)>128:raise ValueError()
            pairs=parse_qsl(target.query,keep_blank_values=True,strict_parsing=True,max_num_fields=2)
            identifier=None
            if action=='refs':
                if len(pairs)!=1 or pairs[0][0]!='asset_id':raise ValueError()
                identifier=_id(pairs[0][1])
            elif pairs:raise ValueError()
            selection=CoverageSelection(action,identifier)
            sources=set();connectors=set()
            def request_allow():
                check()
                if http(request,s,selection) is not True:raise CoverageDenied()
                check()
            def source_allow(scope,t,exp):
                request_allow()
                if source(s,t,exp) is not True:raise CoverageDenied()
                check();sources.add((t,exp));return True
            def connector_allow(scope,kind,value):
                request_allow()
                if connector is None or connector(s,kind,value) is not True:raise CoverageDenied()
                check();connectors.add((kind,value));return True
            def asset_allow(ids):
                request_allow()
                if asset(s,selection,ids) is not True:raise CoverageDenied()
                check()
            request_allow()
            if identifier is not None:asset_allow((identifier,))
            coverage_scope=CoverageScope(*self._identity)
            store=TaskCoverageStore(e,coverage_scope,authorize=source_allow,current=lambda scope:check() or True,
                lifecycle_guard=guard,connector_authorize=connector_allow,max_rows=rows,max_bytes=bytes_)
            service=TaskCoverageService(e,resolve=lambda req,t:store if t==s.task_id else None,
                authorize=lambda req,scope,t,exp:source_allow(scope,t,exp),current=lambda store:check() or True,
                lifecycle_guard=guard,max_response_bytes=bytes_)
            response=service.handle(action,request,{'id':str(s.task_id)})
            if response.status!=200:return response
            result=response.json();ids=()
            if action=='graph':
                values=[n['asset_id'] for n in result['nodes'] if 'asset_id' in n]
                if any(type(v) is not int or not 0<v<2**63 for v in values):raise ValueError()
                ids=tuple(sorted(set(values)))
            elif identifier is not None:ids=(identifier,)
            # No nested guard: the existing service has exited all its contexts.
            for t,exp in tuple(sources):source_allow(coverage_scope,t,exp)
            for kind,value in tuple(connectors):connector_allow(coverage_scope,kind,value)
            asset_allow(ids)
            # Asset disclosure policy is a host callback: it may revoke a
            # previously granted source/connector while evaluating these IDs.
            # Revalidate those exact recorded grants after that boundary.
            for t,exp in tuple(sources):source_allow(coverage_scope,t,exp)
            for kind,value in tuple(connectors):connector_allow(coverage_scope,kind,value)
            request_allow()
            return response
        except PermissionError:return error_response(403,'Coverage disclosure denied')
        except (ValueError,UnicodeError):return error_response(400,'Invalid coverage selector')
        except Exception:return error_response(503,'Coverage read capability unavailable')
