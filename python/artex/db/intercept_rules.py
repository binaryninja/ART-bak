"""Explicit asset/task rule persistence, source asset_intercept/task_intercept.go.

Not tool-input intercept_rules or pending approvals; no matcher, permission gate,
seed, API, cache invalidation or runtime installation. Global scope is explicitly
workspace-wide; task scope never falls back to global. Source ordering and exact
allow normalization preserved (only literal 'allow', otherwise block). Mutation
needs separate host policy and shared lifetime guard; task writes require idle
created/done/failed/timeout parent, no pause/queue/archive. Live changes unsupported.
Source updated_at is a trigger timestamp, NOT a durable policy revision: expected
updated_at is a stale-edit check under row locks, not a distributed runtime lease.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from .core import Query,int64

class RuleDenied(PermissionError):pass
class RuleConflict(RuntimeError):pass
class RuleUnavailable(RuntimeError):pass
class RuleUncertain(RuntimeError):pass
KINDS=frozenset(('exact_domain','exact_ip','exact_url','fuzzy_domain','fuzzy_ip','fuzzy_url','cidr'))
@dataclass(frozen=True)
class RuleScope:
    workspace:str
    task_id:int|None=None
    exploration_id:int|None=None
@dataclass(frozen=True)
class RuleProposal:
    kind:str
    pattern:str
    note:str=''
    enabled:bool=True
    action:str='block'
@dataclass(frozen=True)
class AssetRule:
    id:int
    enabled:bool
    kind:str
    pattern:str
    note:str
    builtin:bool
    action:str
    created_at:object
    updated_at:object

class InterceptRuleStore:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard,mutation_authorize=None,idle=None,max_rows=1000,max_bytes=1048576):
        if type(scope) is not RuleScope or not isinstance(scope.workspace,str) or not 1<=len(scope.workspace)<=256:raise ValueError('Explicit workspace scope required')
        if (scope.task_id is None)!=(scope.exploration_id is None):raise ValueError('Exact task/exploration scope required')
        for v in (scope.task_id,scope.exploration_id):
            if v is not None and int64(v,'rule parent')<=0:raise ValueError('Positive task identity required')
        if any(not callable(v) for v in (authorize,current,lifecycle_guard)) or (mutation_authorize is not None and not callable(mutation_authorize)):raise ValueError('Explicit policy callbacks required')
        if type(max_rows) is not int or not 1<=max_rows<=10000 or type(max_bytes) is not int or not 1024<=max_bytes<=16777216:raise ValueError('Invalid rule bounds')
        self.executor,self.scope=executor,scope;self._executor,self._scope=executor,scope
        self.authorize,self.current,self.guard,self.mutate=authorize,current,lifecycle_guard,mutation_authorize
        self.idle=idle
        self.max_rows,self.max_bytes=max_rows,max_bytes
    def _check(self):
        if self.executor is not self._executor or self.scope is not self._scope:raise RuleDenied('Rule scope changed')
        if self.current(self._scope) is not True:raise RuleDenied('Rule authority stale')
        if self.executor is not self._executor or self.scope is not self._scope:raise RuleDenied('Rule scope changed')
    @contextmanager
    def _access_inner(self,op,proposal=None,rule_id=None):
        with self.guard(self._scope):
            self._check()
            if self.authorize(self._scope,op) is not True:raise RuleDenied('Rule read not authorized')
            self._check()
            if op!='list':
                if not callable(self.mutate) or self.mutate(self._scope,op,rule_id,proposal) is not True:raise RuleDenied('Rule mutation not authorized')
                self._check()
                if not callable(self.idle) or self.idle(self._scope) is not True:raise RuleDenied('Affected policy owners must be idle')
                self._check()
            with self._executor.transaction() as tx:
                if self._scope.task_id is not None:
                    lifecycle=" AND status IN ('created','done','failed','timeout') AND NOT paused AND NOT queued" if op!='list' else ''
                    rows=tx.fetch_all(Query('SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1)'+lifecycle+' FOR UPDATE',(self._scope.task_id,self._scope.exploration_id)))
                    if rows!=[(self._scope.task_id,)]:raise RuleConflict('Task rule parent unavailable')
                self._check();yield tx;self._check()
            try:self._check()
            except BaseException:
                if op!='list':raise RuleUncertain('Rule committed; policy reconciliation required') from None
                raise
    @contextmanager
    def _access(self,op,proposal=None,rule_id=None):
        try:
            with self._access_inner(op,proposal,rule_id) as tx:yield tx
        except (RuleDenied,RuleConflict,RuleUnavailable,RuleUncertain):raise
        except Exception:
            if op!='list':raise RuleUncertain('Rule persistence outcome requires reconciliation') from None
            raise RuleUnavailable('Rule read unavailable') from None
    def _read(self,tx,rule_id=None,lock=False):
        task=self._scope.task_id;table='task_intercept_rules' if task is not None else 'asset_intercept_rules'
        params=[];where=[]
        if task is not None:params.append(task);where.append('task_id=$1')
        if rule_id is not None:params.append(rule_id);where.append('id=$'+str(len(params)))
        params.extend((self.max_bytes,self.max_rows+1));byte='$'+str(len(params)-1);limit='$'+str(len(params))
        # Window on bounded rows before transfer; NULL is overflow, not truncation.
        order='action,id' if task is not None else 'builtin DESC,id DESC'
        fields="id,enabled,kind,pattern,note,"+('false AS builtin,action' if task is not None else "builtin,''::text AS action")+",created_at,updated_at"
        sql=('WITH selected AS (SELECT '+fields+' FROM '+table+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY '+order+' LIMIT '+limit+(' FOR UPDATE' if lock else '')+
            ') SELECT id,enabled,kind,CASE WHEN sum(octet_length(pattern)+octet_length(note)) OVER ()<='+byte+' THEN pattern ELSE NULL END,note,builtin,action,created_at,updated_at FROM selected ORDER BY '+order)
        # Gate note as well: an overflow row must not transfer the other text.
        sql=sql.replace(' END,note,builtin',' END,CASE WHEN sum(octet_length(pattern)+octet_length(note)) OVER ()<='+byte+' THEN note ELSE NULL END,builtin')
        rows=tx.fetch_all(Query(sql,tuple(params)))
        if len(rows)>self.max_rows or any(r[3] is None or r[4] is None for r in rows):raise RuleUnavailable('Rule projection exceeds bound')
        return [AssetRule(*r) for r in rows]
    def list(self):
        with self._access('list') as tx:return self._read(tx)
    def split(self):
        rows=self.list();return [r for r in rows if r.action!='allow'],[r for r in rows if r.action=='allow']
    def _proposal(self,p):
        if type(p) is not RuleProposal or p.kind not in KINDS or type(p.enabled) is not bool:raise ValueError('Typed asset rule required')
        if any(not isinstance(v,str) or '\0' in v for v in (p.pattern,p.note,p.action)) or len((p.pattern+p.note).encode())>self.max_bytes:raise ValueError('Invalid bounded rule text')
        if self._scope.task_id is None and p.action not in ('','block'):raise ValueError('Global allow rule unsupported')
        return RuleProposal(p.kind,p.pattern,p.note,p.enabled,'allow' if p.action=='allow' else 'block')
    def create(self,proposal):
        p=self._proposal(proposal)
        with self._access('create',p) as tx:
            if self._scope.task_id is None:
                sql='INSERT INTO asset_intercept_rules(enabled,kind,pattern,note,builtin) VALUES($1,$2,$3,$4,false) RETURNING id';params=(p.enabled,p.kind,p.pattern,p.note)
            else:
                sql='INSERT INTO task_intercept_rules(task_id,enabled,action,kind,pattern,note) VALUES($1,$2,$3,$4,$5,$6) RETURNING id';params=(self._scope.task_id,p.enabled,p.action,p.kind,p.pattern,p.note)
            rows=tx.fetch_all(Query(sql,params))
            if len(rows)!=1:raise RuleUnavailable('Rule insertion unavailable')
            result=self._read(tx,rows[0][0])
            if len(result)!=1:raise RuleUnavailable('Inserted rule missing')
            return result[0]
    def change(self,rule_id,*,expected_updated_at,proposal=None,enabled=None,delete=False):
        if int64(rule_id,'rule ID')<=0 or expected_updated_at is None:raise ValueError('Rule and expected timestamp required')
        if type(delete) is not bool or sum((proposal is not None,enabled is not None,delete))!=1:raise ValueError('One explicit mutation required')
        if enabled is not None and type(enabled) is not bool:raise ValueError('Boolean toggle required')
        p=self._proposal(proposal) if proposal is not None else enabled
        op='delete' if delete else 'update' if proposal is not None else 'toggle'
        with self._access(op,p,rule_id) as tx:
            rows=self._read(tx,rule_id,True)
            if not rows:return False if delete else None
            if len(rows)!=1 or rows[0].updated_at!=expected_updated_at:raise RuleConflict('Rule changed since read')
            table='task_intercept_rules' if self._scope.task_id is not None else 'asset_intercept_rules'
            params=[rule_id,expected_updated_at];where='id=$1 AND updated_at=$2'
            if self._scope.task_id is not None:params.append(self._scope.task_id);where+=' AND task_id=$3'
            if delete:sql='DELETE FROM '+table+' WHERE '+where
            else:
                values=[('enabled',p.enabled),('kind',p.kind),('pattern',p.pattern),('note',p.note)] if proposal is not None else [('enabled',enabled)]
                if proposal is not None and self._scope.task_id is not None:values.append(('action',p.action))
                assignments=[]
                for key,value in values:params.append(value);assignments.append(key+'=$'+str(len(params)))
                sql='UPDATE '+table+' SET '+','.join(assignments)+' WHERE '+where
            if tx.execute(Query(sql,tuple(params)))!=1:raise RuleConflict('Rule mutation lost ownership')
            if delete:return True
            result=self._read(tx,rule_id)
            if len(result)!=1:raise RuleUnavailable('Updated rule missing')
            return result[0]
