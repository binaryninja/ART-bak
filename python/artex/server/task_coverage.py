"""Opt-in read-only source task coverage endpoints; no default installation.

GET coverage, coverage-graph, asset-refs?asset_id match source routes/DTOs.
Untested backlog remains a store/tool API, not an invented HTTP route. Asset
reads NEVER authorize testing. Actual TaskCoverageStore retains direct source
read grants, nonrecursive scope, terminal-only inherited intent visibility and
separate connector grants. Archived contexts fail503 rather than partial results.
Coverage disabled returns source zero/empty/null metrics; graph/refs keep source
behavior and are not implicitly disabled. Aggregate coverage is approximate.

Explicit executor, resolver, request-aware task/source authorization, current
revision and exact shared guard required. Resolved store must use that SAME guard;
no nested guards or callback-owned global fallback. Existing store policies are
also enforced; HTTP cannot broaden them. Finite response bytes, no writes/tools.
"""
from urllib.parse import urlsplit,parse_qsl
from .core import json_response,error_response
from .task_goals import _id
from ..db.task_coverage import TaskCoverageStore,CoverageScope,CoverageDenied,CoverageUnavailable

class TaskCoverageService:
    def __init__(self,executor,*,resolve,authorize,current,lifecycle_guard,max_response_bytes=2097152):
        if any(not callable(f) for f in (resolve,authorize,current,lifecycle_guard)):raise ValueError('Explicit coverage read authority required')
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=16777216:raise ValueError('Bounded response required')
        self._executor=executor;self.resolve,self.authorize,self.current,self.guard=resolve,authorize,current,lifecycle_guard
        self.max_bytes=max_response_bytes
    def register(self,router):
        for path,action in (('coverage','coverage'),('coverage-graph','graph'),('asset-refs','refs')):
            router.add('GET','/api/tasks/{id}/'+path,lambda req,params,a=action:self.handle(a,req,params))
    def handle(self,action,request,params):
        try:
            if action not in ('coverage','graph','refs') or request.body:raise ValueError()
            task=_id(params['id']);query=urlsplit(request.target).query
            if len(query)>128:raise ValueError()
            pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,max_num_fields=2)
            asset=None
            if action=='refs':
                if len(pairs)!=1 or pairs[0][0]!='asset_id':raise ValueError()
                asset=_id(pairs[0][1])
            elif pairs:raise ValueError()
            original=self.resolve(request,task)
            if original is None:return error_response(404,'task not found')
            if type(original) is not TaskCoverageStore or type(original.scope) is not CoverageScope:raise CoverageUnavailable()
            scope=original.scope
            if scope.task_id!=task:raise CoverageDenied()
            identity=(scope.task_id,scope.exploration_id,scope.revision)
            policies=(original.authorize,original.current,original.guard,original.connector)
            def check():
                if original.executor is not self._executor or original._executor is not self._executor or original._scope is not scope or original.scope is not scope or (scope.task_id,scope.exploration_id,scope.revision)!=identity or original.guard is not self.guard or (original.authorize,original.current,original.guard,original.connector)!=policies:
                    raise CoverageUnavailable('Coverage binding changed')
            def live(s):
                check()
                if self.current(original) is not True or policies[1](s) is not True:raise CoverageDenied()
                check();return True
            def allow(s,t,e):
                live(s)
                if self.authorize(request,s,t,e) is not True:raise CoverageDenied()
                live(s)
                if policies[0](s,t,e) is not True:raise CoverageDenied()
                live(s);return True
            def connector(s,k,v):
                live(s)
                if policies[3] is None or policies[3](s,k,v) is not True:raise CoverageDenied()
                live(s);return True
            check()
            store=TaskCoverageStore(self._executor,scope,authorize=allow,current=live,lifecycle_guard=self.guard,
                connector_authorize=connector,max_rows=original.max_rows,max_bytes=min(original.max_bytes,self.max_bytes))
            if action=='coverage':output=store.coverage()
            elif action=='graph':output=store.coverage_graph()
            else:
                output={'intents':[],'facts':[],'findings':[]}
                buckets={'intent':'intents','fact':'facts','finding':'findings'}
                for row in store.asset_refs(asset):
                    if row.get('kind') not in buckets:raise CoverageUnavailable()
                    output[buckets[row['kind']]].append(row)
            live(scope)
            response=json_response(200,output)
            if len(response.body)>self.max_bytes:raise CoverageUnavailable('Coverage response exceeds bound')
            return response
        except CoverageDenied:return error_response(403,'Task coverage read denied')
        except CoverageUnavailable:return error_response(503,'Task coverage capability unavailable')
        except (ValueError,TypeError,UnicodeError):return error_response(400,'Invalid task coverage request')
        except Exception:return error_response(500,'Task coverage read failed')
