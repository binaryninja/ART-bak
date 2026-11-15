"""Explicit global pool diagnostics/retry policy, no providers or default routes.

Actual Registry Unix timestamps become UTC Go RFC3339 seconds (zero omitted),
not raw floats or epoch placeholders. Raw last_error is always redacted because
provider diagnostics may contain keys/payloads. Metadata reads never select keys,
URLs or headers. Status describes breaker/config state, not provider readiness.

Writes require independent frozen proposal approval, all-owner idle and an exact
shared global guard. coordinate(proposal,apply)->literalTrue MUST synchronously
apply once then invalidate/reapply provider caches and persist/reset health as
needed by the host. No implicit implementation of those effects. Reset-all (id=0) approves a bounded complete registry-ID inventory, including
orphan health IDs but excluding never-recorded profiles. Its memory clear is atomic;
forget callbacks run off-lock and any partial persistence requires reconciliation. Missing adapter
fails503 before effects. Retained/cross-thread apply closures cannot execute.
Failures after apply are uncertain and never automatically retried.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from threading import get_ident
import json,math,time
from ..health import Registry,State
from ..db.settings import SettingsStore
from ..db.profiles import RetryRule
from ..db.core import Query
from .core import json_response,error_response
LAYERS=('connect','empty','stream','breaker','intent')
class PoolDenied(PermissionError):pass
class PoolUnavailable(RuntimeError):pass
@dataclass(frozen=True)
class PoolChange:
    operation:str
    ids:tuple=()
    policy:tuple=()

def _policy(body):
    if type(body) is not dict or set(body)-set(LAYERS):raise ValueError('Invalid retry policy')
    result=[]
    for name in LAYERS:
        row=body.get(name,{})
        if type(row) is not dict or set(row)-{'attempts','interval_ms'}:raise ValueError('Invalid retry layer')
        rule=RetryRule(row.get('attempts',0),row.get('interval_ms',0)).clamped()
        result.append((name,rule.attempts,rule.interval_ms))
    return tuple(result)
def _policy_dict(policy):return {name:dict(attempts=a,interval_ms=i) for name,a,i in policy}
def _timestamp(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise PoolUnavailable()
    try:return datetime.fromtimestamp(math.floor(value),timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    except (ValueError,OverflowError,OSError):raise PoolUnavailable() from None

class PoolHealthService:
    def __init__(self,settings,registry,*,scope,authorize,current,lifecycle_guard,mutation_authorize=None,idle=None,coordinate=None,clock=time.time,max_rows=1000,max_bytes=1048576):
        if type(settings) is not SettingsStore or type(registry) is not Registry or not isinstance(scope,str) or not 1<=len(scope)<=256:raise ValueError('Explicit actual global resources required')
        if any(not callable(v) for v in (authorize,current,lifecycle_guard,clock)):raise ValueError('Explicit read/lifetime policies required')
        if type(max_rows) is not int or not 1<=max_rows<=10000 or type(max_bytes) is not int or not 1024<=max_bytes<=16777216:raise ValueError('Invalid bounds')
        self.settings,self.registry=settings,registry;self._settings,self._registry=settings,registry;self._executor=settings.executor
        self.scope=scope;self._scope=scope;self.authorize,self.current,self.guard=authorize,current,lifecycle_guard;self._guard=lifecycle_guard
        self.mutate,self.idle,self.coordinate=mutation_authorize,idle,coordinate;self.clock=clock;self.max_rows,self.max_bytes=max_rows,max_bytes
    def _check(self):
        if self.settings is not self._settings or self.registry is not self._registry or self.settings.executor is not self._executor or self.guard is not self._guard or self.scope!=self._scope:raise PoolDenied()
        if self.current(self.scope) is not True:raise PoolDenied()
        if self.settings is not self._settings or self.registry is not self._registry or self.settings.executor is not self._executor or self.guard is not self._guard or self.scope!=self._scope:raise PoolDenied()
    def _setting(self,key):
        rows=self._executor.fetch_all(Query('SELECT CASE WHEN octet_length(value)<=$2 THEN value ELSE NULL END FROM settings WHERE key=$1',(key,self.max_bytes)))
        if not rows:return None
        if len(rows)!=1 or rows[0][0] is None:raise PoolUnavailable()
        return rows[0][0]
    def _retry(self):
        raw=self._setting('llm_retry_policy')
        if not raw:return _policy_dict(_policy({}))
        try:body=json.loads(raw)
        except (ValueError,TypeError):return _policy_dict(_policy({}))
        if type(body) is dict and set(body)-set(LAYERS):raise PoolUnavailable()
        try:return _policy_dict(_policy(body))
        except (ValueError,TypeError):return _policy_dict(_policy({}))  # source malformed types use builtin defaults
    def _status(self):
        enabled=self._setting('llm_pool_enabled') in ('true','1');fallback=self._setting('llm_pool_bind_fallback') in ('true','1')
        rows=self._executor.fetch_all(Query("""WITH profiles AS (SELECT id,name,model,format,priority,is_default,pool_exclude FROM llm_profiles)
SELECT id,CASE WHEN sum(octet_length(name)+octet_length(model)+octet_length(format)) OVER ()<=$1 THEN name ELSE NULL END,
CASE WHEN sum(octet_length(name)+octet_length(model)+octet_length(format)) OVER ()<=$1 THEN model ELSE NULL END,
CASE WHEN sum(octet_length(name)+octet_length(model)+octet_length(format)) OVER ()<=$1 THEN format ELSE NULL END,
priority,is_default,pool_exclude FROM profiles ORDER BY is_default DESC,priority DESC,id ASC LIMIT $2""",(self.max_bytes,self.max_rows+1)))
        if len(rows)>self.max_rows:raise PoolUnavailable()
        now=self.clock();_timestamp(now);chain=[]
        for id,name,model,format,priority,active,excluded in rows:
            if any(v is None for v in (name,model,format)) or type(id) is not int or id<=0 or type(active) is not bool or type(excluded) is not bool:raise PoolUnavailable()
            st=self.registry.get(id)
            if type(st) is not State or any(type(v) is not int or v<0 for v in (st.fails,st.trips)):raise PoolUnavailable()
            _timestamp(st.open_until);_timestamp(st.last_at)
            opened=now<st.open_until
            item=dict(profile_id=str(id),name=name,model=model,format=format,priority=priority,active=active,excluded=excluded,state='tripped' if opened else 'degraded' if st.fails else 'ok',fails=st.fails,trips=st.trips,cooldown_secs=int(st.open_until-now)+1 if opened else 0)
            if st.last_error:item['last_error']='[redacted]'
            if st.last_at:item['last_at']=_timestamp(st.last_at)
            chain.append(item)
        return dict(enabled=enabled,bind_fallback=fallback,chain=chain)
    def _write(self,request,change,inventory=None):
        if any(not callable(v) for v in (self.mutate,self.idle,self.coordinate)):raise PoolUnavailable()
        if self.mutate(request,self.scope,change) is not True:raise PoolDenied()
        self._check()
        if self.idle(self.scope) is not True:raise PoolDenied()
        self._check();active=True;calls=0;completed=False;owner=get_ident()
        def apply():
            nonlocal calls,completed
            if not active or get_ident()!=owner:raise PoolUnavailable()
            calls+=1
            if calls!=1:raise PoolUnavailable()
            self._check()
            if self.idle(self.scope) is not True:raise PoolDenied()
            self._check()
            if change.operation=='reset_all':
                if inventory is None or inventory[1]!=change.ids or self.registry.inventory(self.max_rows)!=inventory:raise PoolUnavailable()
                self._check()
                self.registry.reset_inventory(inventory,self.max_rows,current=self._check)
            elif change.operation=='reset':
                for id in change.ids:self._check();self.registry.reset(id)
            else:
                with self._executor.transaction():
                    self.settings.set('llm_retry_policy',json.dumps(_policy_dict(change.policy),separators=(',',':')))
                    self._check()
            self._check();completed=True;return True
        try:
            confirmed=self.coordinate(change,apply)
            if confirmed is not True or calls!=1 or not completed:raise PoolUnavailable()
            self._check()
        except BaseException:
            if calls:raise _PoolUncertain() from None
            raise
        finally:active=False
    def register(self,router):
        for method,path,op in (('GET','/api/llm/pool','status'),('POST','/api/llm/pool/reset','reset'),('GET','/api/llm/retry-policy','retry'),('POST','/api/llm/retry-policy','save')):
            router.add(method,path,lambda req,params,a=op:self.handle(a,req))
    def handle(self,op,request):
        changed=False
        read_authorize=self.authorize
        def read_grant():
            if self.authorize is not read_authorize:raise PoolDenied()
            self._check()
            if read_authorize(request,self.scope,op) is not True:raise PoolDenied()
            if self.authorize is not read_authorize:raise PoolDenied()
            self._check()
        try:
            from urllib.parse import urlsplit
            if urlsplit(request.target).query:raise ValueError()
            with self._guard(self.scope):
                self._check()
                if self.authorize(request,self.scope,op) is not True:raise PoolDenied()
                self._check()
                if op in ('status','retry'):
                    if request.body:raise ValueError()
                elif op=='reset':
                    body=request.json_object(4096);id=body.get('id',0)
                    if set(body)-{'id'} or type(id) is not int or not 0<=id<2**63:raise ValueError()
                    try:inventory=self.registry.inventory(self.max_rows) if id==0 else None
                    except ValueError:raise PoolUnavailable() from None
                    ids=inventory[1] if inventory is not None else (id,)
                    if len(ids)>self.max_rows or any(type(i) is not int or i<=0 for i in ids):raise PoolUnavailable()
                    self._write(request,PoolChange('reset_all' if id==0 else 'reset',ids=ids),inventory);changed=True
                elif op=='save':self._write(request,PoolChange('save',policy=_policy(request.json_object(65536))));changed=True
                else:raise ValueError()
                result=self._retry() if op in ('retry','save') else self._status()
                self._check();response=json_response(200,result)
                if len(response.body)>self.max_bytes:raise PoolUnavailable()
                if op in ('status','retry'):
                    read_grant()
                else:
                    return response
            # Read grants may be revoked by snapshot or guard cleanup callbacks.
            # This is not a second mutation approval and creates no nested guard.
            read_grant()
            return response
        except _PoolUncertain:return json_response(503,{'error':'Pool changes require reconciliation','committed':None,'needs_reconciliation':True})
        except Exception as exc:
            if changed:return json_response(503,{'error':'Pool changed; reconciliation required','committed':True,'needs_reconciliation':True})
            if isinstance(exc,PoolDenied):return error_response(403,'Pool operation not authorized')
            if isinstance(exc,(ValueError,UnicodeError)):return error_response(400,'Invalid pool request')
            return error_response(503,'Pool operation unavailable')
class _PoolUncertain(RuntimeError):pass
