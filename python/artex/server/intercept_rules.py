"""Optional source task/global asset rule metadata routes; no enforcement wiring.

Fixed store is either one exact task/exploration or an explicitly authorized
whole-workspace global catalog. No resolver fallback. Store's separate mutation
and all-affected-owner idle gates remain mandatory. Shared guard must be reentrant.
Source pattern validation happens before any proposal authorization. Unlike source,
update/toggle/delete REQUIRE X-Rule-Updated-At with the exact timezone-aware ISO
updated_at returned by list/create. This is stale-edit CAS, not a runtime revision.
No tool permission, matcher/cache synchronization, live policy reload or defaults.
"""
from dataclasses import dataclass,asdict
from datetime import datetime
import ipaddress,re
from urllib.parse import urlsplit
from ..db.intercept_rules import InterceptRuleStore,RuleProposal,AssetRule,KINDS,RuleDenied,RuleConflict,RuleUnavailable,RuleUncertain
from .core import json_response,error_response

@dataclass(frozen=True)
class RuleSelection:
    operation:str
    rule_id:int|None=None
    proposal:RuleProposal|None=None
    enabled:bool|None=None
    expected_updated_at:datetime|None=None

# Go strings.TrimSpace uses Unicode White_Space, unlike Python's additional C0s.
_SPACE='\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'
def proposal(body,task):
    fields={'enabled','kind','pattern','note'}|({'action'} if task else set())
    if set(body)-fields:raise ValueError('Unsupported field')
    kind=body.get('kind','');pattern=body.get('pattern','');note=body.get('note','');enabled=body.get('enabled',False)
    action=body.get('action','')
    if action=='':action='block'
    if type(enabled) is not bool or any(not isinstance(v,str) for v in (kind,pattern,note,action)) or kind not in KINDS or action not in ('block','allow'):raise ValueError('Invalid rule')
    pattern=pattern.strip(_SPACE)
    if not pattern or '\0' in pattern+note or len((pattern+note).encode('utf-8'))>1048576:raise ValueError('Invalid pattern')
    if kind=='exact_ip':
        if '%' in pattern:raise ValueError('Scoped IP unavailable')
        ipaddress.ip_address(pattern)
    elif kind=='cidr':
        if '%' in pattern or '/' not in pattern or not re.fullmatch('[0-9]+',pattern.rsplit('/',1)[1]):raise ValueError('Invalid CIDR')
        ipaddress.ip_network(pattern,strict=False)
    return RuleProposal(kind,pattern,note,enabled,action)

def _id(raw):
    if not isinstance(raw,str) or not re.fullmatch('[1-9][0-9]{0,18}',raw) or int(raw)>=2**63:raise ValueError('Invalid identity')
    return int(raw)
def _dto(rule):
    if type(rule) is not AssetRule:raise RuleUnavailable('Invalid rule DTO')
    result=asdict(rule)
    for key in ('created_at','updated_at'):
        value=result[key]
        if not isinstance(value,datetime) or value.tzinfo is None:raise RuleUnavailable('Invalid rule timestamp')
        result[key]=value.isoformat().replace('+00:00','Z')
    if not result['action']:result.pop('action')
    return result

class InterceptRuleService:
    def __init__(self,store,*,authorize,mutation_authorize=None,max_response_bytes=2097152):
        if type(store) is not InterceptRuleStore or not callable(authorize) or (mutation_authorize is not None and not callable(mutation_authorize)):raise ValueError('Actual store and explicit request authority required')
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=16777216:raise ValueError('Invalid response bound')
        self.store=store;self._store=store;self._executor=store.executor;self._scope=store.scope;self._guard=store.guard
        self.authorize,self.mutate=authorize,mutation_authorize;self.max_response_bytes=max_response_bytes
    def register(self,router):
        # Explicit correlated paths keep task/global parameter names unambiguous.
        for task_scope,method,path,op in (
            (True,'GET','/api/tasks/{id}/intercept-rules','list'),
            (True,'POST','/api/tasks/{id}/intercept-rules','create'),
            (True,'PUT','/api/tasks/{id}/intercept-rules/{rid}','update'),
            (True,'DELETE','/api/tasks/{id}/intercept-rules/{rid}','delete'),
            (True,'POST','/api/tasks/{id}/intercept-rules/{rid}/toggle','toggle'),
            (False,'GET','/api/asset-intercept/rules','list'),
            (False,'POST','/api/asset-intercept/rules','create'),
            (False,'PUT','/api/asset-intercept/rules/{id}','update'),
            (False,'DELETE','/api/asset-intercept/rules/{id}','delete'),
            (False,'POST','/api/asset-intercept/rules/{id}/toggle','toggle')):
            if task_scope != (self._scope.task_id is not None):continue
            router.add(method,path,lambda req,params,a=op:self.handle(a,req,params))
    def _check(self):
        if self.store is not self._store or self.store.executor is not self._executor or self.store.scope is not self._scope or self.store.guard is not self._guard:raise RuleDenied('Rule store binding changed')
        self.store._check()
        if self.store is not self._store or self.store.scope is not self._scope or self.store.guard is not self._guard or self.store.executor is not self._executor:raise RuleDenied('Rule store binding changed')
    def handle(self,op,request,params):
        committed=False
        try:
            if urlsplit(request.target).query:raise ValueError('Query unsupported')
            task=self._scope.task_id is not None
            if task and _id(params.get('id',''))!=self._scope.task_id:raise RuleDenied('Foreign task scope')
            rid=_id(params.get('rid' if task else 'id','')) if op in ('update','delete','toggle') else None
            expected=None
            if rid is not None:
                raw=request.header('X-Rule-Updated-At')
                if not raw:raise RuleConflict('Expected timestamp required')
                if len(raw)>64:raise ValueError('Invalid timestamp')
                expected=datetime.fromisoformat(raw.replace('Z','+00:00'))
                if expected.tzinfo is None:raise ValueError('Timezone required')
            p=None;enabled=None
            if op in ('create','update'):p=proposal(request.json_object(),task)
            elif op=='toggle':
                body=request.json_object()
                if set(body)-{'enabled'} or type(body.get('enabled',False)) is not bool:raise ValueError('Invalid toggle')
                enabled=body.get('enabled',False)
            elif op not in ('list','delete') or request.body:raise ValueError('Unsupported operation')
            selection=RuleSelection(op,rid,p,enabled,expected)
            with self._guard(self._scope):
                self._check()
                if self.authorize(request,self._scope,selection) is not True:raise RuleDenied('Request not authorized')
                self._check()
                if op!='list':
                    if not callable(self.mutate) or self.mutate(request,self._scope,selection) is not True:raise RuleDenied('Mutation not authorized')
                    self._check()
                if op=='list':result={'rules':[_dto(r) for r in self.store.list()]}
                elif op=='create':
                    row=self.store.create(p);committed=True;result=_dto(row)
                else:
                    row=self.store.change(rid,expected_updated_at=expected,proposal=p,enabled=enabled,delete=op=='delete');committed=True;self._check()
                    if op=='delete':result={'deleted':row if task else rid}
                    elif row is None:return error_response(404,'Rule not found')
                    elif op=='toggle':result={'ok':True,'enabled':enabled}
                    else:result=_dto(row)
                self._check();response=json_response(200,result)
                if len(response.body)>self.max_response_bytes:raise RuleUnavailable('Response bound exceeded')
                return response
        except RuleUncertain:return json_response(503,{'error':'Rule state requires reconciliation','committed':None,'needs_reconciliation':True})
        except BaseException as exc:
            if committed:return json_response(503,{'error':'Rule committed; reconciliation required','committed':True,'needs_reconciliation':True})
            if not isinstance(exc,Exception):raise
            if isinstance(exc,RuleDenied):return error_response(403,'Rule operation not authorized')
            if isinstance(exc,RuleConflict):return error_response(409,'Rule or task changed; reload before editing')
            if isinstance(exc,(ValueError,UnicodeError)):return error_response(400,'Invalid rule request')
            return error_response(503,'Rule operation unavailable')
