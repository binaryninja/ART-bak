"""Explicit source-native set_constraints tool, never an authorization source.

agent/tools.go accepts text/type or constraints[], trims text, normalizes type,
and defaults omitted/empty type to deny. This port retains wire values/output,
but strictly validates bounded batches and commits all rows atomically instead
of source per-item partial success. No overwrite, deletion or default install.

Model-extracted allow text does NOT grant permission. Host proposal_authorize
must confirm each exact normalized proposal against independently authorized
operator instructions, including denials (which can also disrupt legitimate
work). Every future tool still needs its own fresh scope/permission approval.
The mandatory shared guard/current fence task/run/revision and config lifetime;
SQL locks supplement, not replace, that authority. Host owns origin attribution.
No durable tool receipt by itself: use inside a durable caller such as GoalPlanner
before enabling retries; ambiguous outcomes must be reconciled, not retried.
"""
from dataclasses import dataclass
from copy import deepcopy
import json
from ..agent.runtime import Tool,Action,Grant
from ..db.core import Query,int64
from ..db.constraints import ConstraintStore
from ..db.exploration import ExplorationStore
from ..db.tool_catalog import value_json

class ConstraintToolDenied(RuntimeError):pass

@dataclass(frozen=True)
class ConstraintScope:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str
    origin:str

@dataclass(frozen=True)
class Proposal:
    kind:str
    text:str

_SCHEMA={'type':'object','properties':{'constraints':{'type':'array','items':{'type':'object'}},'text':{'type':'string'},'type':{'type':'string'}},'additionalProperties':False}

def parse_constraints(args):
    if not isinstance(args,dict) or set(args)-set(_SCHEMA['properties']):raise ValueError('Invalid constraint fields')
    batch=args.get('constraints')
    if batch is not None and not isinstance(batch,list):raise ValueError('constraints must be an array')
    items=batch if batch else [{k:v for k,v in args.items() if k!='constraints'}]
    if not 1<=len(items)<=32:raise ValueError('One to 32 constraints required')
    result=[]
    for item in items:
        if not isinstance(item,dict) or set(item)-{'text','type'}:raise ValueError('Invalid constraint fields')
        text=item.get('text');kind=item.get('type','')
        if not isinstance(text,str) or not text.strip() or '\0' in text or len(text.encode('utf-8'))>16384:raise ValueError('Bounded nonempty constraint text required')
        if not isinstance(kind,str):raise ValueError('Constraint type must be text')
        kind=kind.strip().lower() or 'deny'
        if kind not in ('allow','deny'):raise ValueError('Constraint type must be allow or deny')
        result.append(Proposal(kind,text.strip()))
    return tuple(result)

def _grant(value):
    if not isinstance(value,Grant) or any(v is not True for v in (value.permission,value.in_scope,value.approved)):
        raise ConstraintToolDenied('Constraint action not authorized')

class ConstraintTools:
    def __init__(self,store,graph,scope,*,current,lifecycle_guard,authorize,proposal_authorize):
        if not isinstance(store,ConstraintStore) or not isinstance(graph,ExplorationStore) or graph.read_only or not isinstance(scope,ConstraintScope):raise ValueError('Actual scoped stores and constraint scope required')
        if store.executor is not graph.executor or store.exploration_id!=graph.exploration_id or scope.exploration_id!=store.exploration_id:raise ValueError('Constraint and graph executor/scope mismatch')
        for value in (scope.task_id,scope.exploration_id,scope.run_id):
            if int64(value,'constraint scope ID')<=0:raise ValueError('Positive scope ID required')
        for value,bound in ((scope.revision,256),(scope.origin,128)):
            if not isinstance(value,str) or not value.strip() or '\0' in value or len(value.encode('utf-8'))>bound:raise ValueError('Bounded revision and host origin required')
        if any(not callable(f) for f in (current,lifecycle_guard,authorize,proposal_authorize)):raise ValueError('Explicit lifecycle and proposal authorization required')
        self.store,self.graph,self.scope=store,graph,scope
        self._executor=store.executor
        self._scope=scope
        self.current,self.guard,self.authorize,self.proposals=current,lifecycle_guard,authorize,proposal_authorize
    def _check(self,context):
        def identity():
            if not isinstance(context,dict) or self.scope!=self._scope:raise ConstraintToolDenied('Constraint runtime scope required')
            for key in ('task_id','exploration_id','run_id','revision'):
                value=getattr(self._scope,key)
                if type(context.get(key)) is not type(value) or context[key]!=value:raise ConstraintToolDenied('Constraint runtime scope mismatch')
        context.check();identity()
        if self.current(self.scope) is not True:raise ConstraintToolDenied('Constraint lifetime changed')
        if self.store.executor is not self._executor or self.graph.executor is not self._executor or self.store.exploration_id!=self.scope.exploration_id or self.graph.exploration_id!=self.scope.exploration_id or self.graph.read_only:raise ConstraintToolDenied('Constraint store binding changed')
        context.check();identity()
    def tools(self):
        def validate(args):parse_constraints(args);return True
        return (Tool('set_constraints','Register explicitly authorized operator constraints, not inferred permission. Atomic batch; omitted type defaults to deny.',deepcopy(_SCHEMA),self._dispatch,validate),)
    def _dispatch(self,context,args):
        snapshot=value_json(args);items=parse_constraints(snapshot)
        action=Action('tool','set_constraints',json.dumps(snapshot,separators=(',',':'),allow_nan=False))
        with self.guard(self.scope):
            self._check(context);_grant(self.authorize(context,action));self._check(context)
            # Immutable normalized proposals; neither gate can rewrite approved SQL.
            _grant(self.proposals(self.scope,items));self._check(context)
            with self.store.executor.transaction() as tx:
                rows=tx.fetch_all(Query("SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND status IN ('created','running') AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE",(self.scope.task_id,self.scope.exploration_id)))
                if rows not in ([(self.scope.task_id,)],[[self.scope.task_id]]):raise ConstraintToolDenied('Constraint parent unavailable')
                store=ConstraintStore(tx,self.scope.exploration_id);ids=[]
                for item in items:
                    self._check(context);id=store.add(item.kind,item.text,self.scope.origin)
                    if id<=0:raise ConstraintToolDenied('Constraint insert not confirmed')
                    ids.append(id)
                self._check(context)
                return json.dumps({'ids':ids}) if snapshot.get('constraints') else 'constraint added: '+str(ids[0])
