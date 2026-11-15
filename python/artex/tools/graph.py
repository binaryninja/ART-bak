"""Explicit local graph tools from agent/tools.go, never task completion.

record_fact uses source confirmed/priority5 payload and intent-yields lineage;
prove_goal requires local fact/finding evidence and persists proves + met atomically.
Unlike source ignored write errors/partial fact batches, every failure rolls back.
No GoalMet/global completion side effect, inherited-node mutation or inherited
read union. list_facts advertises LOCAL scope, summary160, source cursor/defaults.
No intent state mutations here, so WorkerStore generation remains host-owned.
Mandatory guard/current must fence task/run/revision and, for worker tools, intent
version/state ownership. SQL parent locks are additional checks, not that authority.
Grants authorize exact immutable arguments, not catalog discovery or model claims.
Evidence truth is host authorization policy; this module validates structural
local evidence linkage and never claims independent verification of assertions.
"""
from dataclasses import dataclass
from copy import deepcopy
import json
from ..agent.runtime import Tool,Action,Grant
from ..db.core import Query,int64
from ..db.exploration import ExplorationStore,NODE_COLS,Node
from ..db.tool_catalog import value_json

class GraphToolDenied(RuntimeError):pass
class GraphToolInvalid(ValueError):pass

@dataclass(frozen=True)
class GraphScope:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str
    worker:str
    intent_id:int=0


def _id(value):
    if int64(value,'graph ID')<=0:raise GraphToolInvalid('Positive graph ID required')
    return value

def _text(value,limit=65536):
    if not isinstance(value,str) or '\0' in value or len(value.encode('utf-8'))>limit:raise GraphToolInvalid('Invalid bounded text')
    return value

def _grant(value):
    if not isinstance(value,Grant) or any(v is not True for v in (value.permission,value.in_scope,value.approved)):raise GraphToolDenied('Graph action denied')

FACT_FIELDS={'summary','detail','evidence','confidence','intent_id','asset_ids'}
SCHEMAS={
'record_fact':{'type':'object','properties':{**{k:{'type':'string'} for k in ('summary','detail','evidence','confidence')},'intent_id':{'type':'integer'},'asset_ids':{'type':'array','items':{'type':'integer'}},'facts':{'type':'array','items':{'type':'object'}}},'additionalProperties':False},
'list_facts':{'type':'object','properties':{'limit':{'type':'integer'},'before':{'type':'integer'},'q':{'type':'string'}},'additionalProperties':False},
'list_goals':{'type':'object','properties':{},'additionalProperties':False},
'prove_goal':{'type':'object','properties':{'goal_id':{'type':'integer'},'evidence_id':{'type':'integer'},'reason':{'type':'string'}},'required':['goal_id','evidence_id'],'additionalProperties':False},
}

class GraphTools:
    def __init__(self,store,scope,*,current,lifecycle_guard,authorize,asset_authorize):
        if not isinstance(store,ExplorationStore) or store.read_only or not isinstance(scope,GraphScope):raise GraphToolInvalid('Writable local graph and explicit scope required')
        for v in (scope.task_id,scope.exploration_id,scope.run_id):_id(v)
        if scope.intent_id:_id(scope.intent_id)
        if scope.exploration_id!=store.exploration_id or not _text(scope.revision,256) or not _text(scope.worker,128):raise GraphToolInvalid('Graph scope mismatch')
        if any(not callable(v) for v in (current,lifecycle_guard,authorize,asset_authorize)):raise GraphToolInvalid('Explicit lifecycle and grant adapters required')
        self.store,self.scope=store,scope;self.current,self.guard,self.authorize,self.assets=current,lifecycle_guard,authorize,asset_authorize
    def _check(self,context):
        context.check()
        if self.current(self.scope) is not True:raise GraphToolDenied('Graph lifetime changed')
    def _facts(self,args):
        batch=args.get('facts')
        if batch is None:batch=[{k:v for k,v in args.items() if k!='facts'}]
        if not isinstance(batch,list) or not 1<=len(batch)<=20:raise GraphToolInvalid('One to twenty facts required')
        result=[]
        for item in batch:
            if not isinstance(item,dict) or set(item)-FACT_FIELDS:raise GraphToolInvalid('Invalid fact fields')
            summary=_text(item.get('summary'),16384)
            if not summary.strip():raise GraphToolInvalid('Fact summary required')
            payload={'summary':summary}
            for key in ('detail','evidence','confidence'):
                value=_text(item.get(key,''))
                if key!='detail':value=value.strip()
                if value:payload[key]=value
            if payload.get('confidence','') not in ('','observed','inferred'):raise GraphToolInvalid('Invalid confidence')
            intent=item.get('intent_id',args.get('intent_id',self.scope.intent_id))
            if intent:_id(intent)
            elif type(intent) is not int:raise GraphToolInvalid('Invalid intent ID')
            if self.scope.intent_id and intent!=self.scope.intent_id:raise GraphToolDenied('Fact belongs to another worker intent')
            assets=item.get('asset_ids',[])
            if not isinstance(assets,list) or len(assets)>100:raise GraphToolInvalid('Bounded asset IDs required')
            assets=list(dict.fromkeys(_id(v) for v in assets))
            result.append((payload,intent,assets))
        return result
    def _validate(self,name,args):
        if not isinstance(args,dict) or set(args)-set(SCHEMAS[name]['properties']):raise GraphToolInvalid('Invalid graph arguments')
        if name=='record_fact':self._facts(args)
        elif name=='prove_goal':_id(args.get('goal_id'));_id(args.get('evidence_id'));_text(args.get('reason',''))
        elif name=='list_facts':
            for key in ('before','limit'):
                if type(args.get(key,0)) is not int:raise GraphToolInvalid('Integer pagination required')
                int64(args.get(key,0),key)
            _text(args.get('q',''),1024)
        return True
    def tools(self):
        descriptions={'record_fact':'Record local observed/inferred facts with evidence and intent lineage. Batch writes are atomic.','list_facts':'Page LOCAL facts only, newest first; inherited facts are not included.','list_goals':'List local persisted goal nodes and open/met state.','prove_goal':'Persist local fact/finding evidence linkage and mark one goal met. Never completes the task.'}
        return tuple(Tool(n,descriptions[n],deepcopy(s),lambda c,a,n=n:self._dispatch(n,c,a),lambda a,n=n:self._validate(n,a)) for n,s in SCHEMAS.items())
    def _dispatch(self,name,context,args):
        args=value_json(args);self._validate(name,args)
        action=Action('tool',name,json.dumps(args,allow_nan=False,separators=(',',':')))
        with self.guard(self.scope):
            self._check(context);_grant(self.authorize(context,action));self._check(context)
            args=action.arguments()
            with self.store.executor.transaction() as tx:
                parents=tx.fetch_all(Query('''SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE''',(self.scope.task_id,self.scope.exploration_id)))
                if parents not in ([(self.scope.task_id,)],[[self.scope.task_id]]):raise GraphToolDenied('Local task unavailable')
                graph=ExplorationStore(tx,self.scope.exploration_id)
                if name=='record_fact':result=self._record(graph,context,args)
                elif name=='prove_goal':result=self._prove(graph,context,args)
                elif name=='list_goals':result=[n.to_dict() for n in graph.list_by_kind('goal',100)]
                else:result=self._list(graph,args)
                self._check(context)
            return result if isinstance(result,str) else json.dumps(result,ensure_ascii=False,allow_nan=False)
    def _node(self,graph,id,kinds):
        rows=graph.executor.fetch_all(Query('SELECT '+NODE_COLS+' FROM exploration_nodes WHERE id=$1 AND exploration_id=$2 FOR UPDATE',(id,self.scope.exploration_id)))
        if not rows or rows[0][1] not in kinds:raise GraphToolDenied('Local node kind or ownership mismatch')
        return Node(*rows[0])
    def _record(self,graph,context,args):
        ids=[]
        for payload,intent,assets in self._facts(args):
            self._check(context);_grant(self.assets(self.scope,tuple(assets)));self._check(context)
            if intent:self._node(graph,intent,('intent',))
            id=graph.add_node('fact',payload,5,'confirmed',self.scope.worker,assets)
            if intent and not graph.link(intent,'yields',id):raise GraphToolDenied('Fact lineage write failed')
            ids.append(id)
        return {'ids':ids} if 'facts' in args else 'fact recorded: '+str(ids[0])
    def _prove(self,graph,context,args):
        goal=args['goal_id'];evidence=args['evidence_id']
        # Stable lock order avoids evidence/goal inversion between concurrent calls.
        for id in sorted({goal,evidence}):self._node(graph,id,('goal',) if id==goal else ('fact','finding'))
        if goal==evidence:raise GraphToolDenied('Goal is not its own evidence')
        if not graph.link(evidence,'proves',goal):
            existing=graph.executor.fetch_all(Query("SELECT 1 FROM exploration_edges WHERE exploration_id=$1 AND src_id=$2 AND rel='proves' AND dst_id=$3",(self.scope.exploration_id,evidence,goal)))
            if not existing:raise GraphToolDenied('Evidence link persistence failed')
        self._check(context);graph.set_node_state(goal,'met')
        return 'goal '+str(goal)+' marked met'
    def _list(self,graph,args):
        limit=args.get('limit',20);limit=20 if limit<=0 else min(limit,100);before=args.get('before',0);q=args.get('q','').strip()
        where=" WHERE exploration_id=$1 AND kind='fact' AND ($2='' OR COALESCE(payload->>'summary','') ILIKE '%' || $2 || '%')"
        total=graph.executor.fetch_one(Query('SELECT COUNT(*) FROM exploration_nodes'+where,(self.scope.exploration_id,q)))[0]
        rows=graph.executor.fetch_all(Query('SELECT '+NODE_COLS+' FROM exploration_nodes'+where+' AND ($3<=0 OR id<$3) ORDER BY id DESC LIMIT $4',(self.scope.exploration_id,q,before,limit+1)))
        facts=[]
        for row in rows[:limit]:
            n=Node(*row);summary=n.payload.get('summary') if isinstance(n.payload,dict) else None;item={'id':n.id,'state':n.state,'summary':summary}
            if isinstance(summary,str):item['summary']=summary[:160]+('…' if len(summary)>160 else '')
            if isinstance(summary,str) and len(summary)>160:item['summary_truncated']=True
            facts.append(item)
        out=dict(facts=facts,total=total,has_more=len(rows)>limit)
        if out['has_more'] and facts:out['next_before']=facts[-1]['id']
        return out
