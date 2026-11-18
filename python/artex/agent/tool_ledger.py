"""Durable tool-call receipts and source-compatible activity projections."""
import hashlib
import json
from ..db.core import Query
from .runtime import EffectUncertain

DDL='''CREATE TABLE IF NOT EXISTS artex_tool_calls (
 call_key TEXT PRIMARY KEY,task_id BIGINT NOT NULL,exploration_id BIGINT NOT NULL,
 run_id BIGINT NOT NULL,session_id TEXT NOT NULL,turn_id TEXT NOT NULL,
 tool_call_id TEXT NOT NULL,tool TEXT NOT NULL,agent TEXT NOT NULL,
 use_activity_id BIGINT NOT NULL,result_activity_id BIGINT,status TEXT NOT NULL,
 started_at TIMESTAMPTZ NOT NULL DEFAULT now(),completed_at TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS artex_tool_calls_task ON artex_tool_calls(task_id,run_id);'''

class ToolLedger:
    def __init__(self,executor,*,redact,configuration_current,max_bytes=16384):
        self.executor,self.redact,self.configuration_current=executor,redact,configuration_current
        self.max_bytes=max_bytes

    def bounded(self,text):
        value=self.redact(text);raw=value.encode('utf-8')
        return value if len(raw)<=self.max_bytes else raw[:self.max_bytes-64].decode('utf-8','ignore')+'\n[Full evidence retained by the owning tool]'

    @staticmethod
    def invoke(context,action,callback,record):
        record(context,action,'started',None,False)
        try:output=callback()
        except EffectUncertain:
            record(context,action,'uncertain','Effect cleanup is unconfirmed.',True);raise
        except Exception:
            record(context,action,'failed','Nested tool call failed.',True);raise
        record(context,action,'completed',output,False)
        return output

    def bind(self,scope,role,*,current,guard):
        def record(context,action,state,output,is_error):
            # The captured scope is host-owned. A callback cannot choose another
            # task, generation, session, tool ID, or result row.
            for name in ('task_id','exploration_id','run_id','revision'):
                if context.get(name)!=getattr(scope,name):raise EffectUncertain('Tool ledger scope changed')
            session=context.get('session_id');turn=context.get('chat_turn_id')
            if session!=scope.session_id or not isinstance(turn,str) or not turn or not action.call_id:raise EffectUncertain('Tool ledger identity unavailable')
            key=hashlib.sha256(json.dumps([scope.task_id,scope.run_id,session,turn,action.call_id]).encode()).hexdigest()
            call_id='artex:'+key
            node=getattr(scope,'intent_id',None) or None
            worker=getattr(scope,'owner',role)
            metadata=json.dumps({'_artex_tool_call':{'key':key,'task_id':scope.task_id,'run_id':scope.run_id,'session_id':session,'turn_id':turn,'role':role,'state':state}})
            try:
                with guard(scope):
                    if not self.configuration_current({'task_id':scope.task_id}):raise EffectUncertain('Tool ledger configuration changed')
                    if state=='started':
                        context.check()
                        if current(scope) is not True:raise EffectUncertain('Tool ledger ownership changed')
                    with self.executor.transaction() as tx:
                        row=tx.fetch_one(Query('SELECT t.exploration_id,b.run_id FROM tasks t JOIN artex_task_bootstrap b ON b.task_id=t.id WHERE t.id=$1 AND t.deleted_at IS NULL FOR UPDATE OF t',(scope.task_id,)))
                        if row!=(scope.exploration_id,scope.run_id):raise EffectUncertain('Tool ledger generation changed')
                        prior=tx.fetch_all(Query('SELECT status FROM artex_tool_calls WHERE call_key=$1 FOR UPDATE',(key,)))
                        if state=='started':
                            if prior:raise EffectUncertain('Tool invocation already admitted')
                            detail=self.bounded(action.input_json)
                        else:
                            if prior!=[('started',)]:raise EffectUncertain('Tool completion has no original admission')
                            detail=self.bounded(output or '')
                        activity=tx.fetch_one(Query('INSERT INTO activity(exploration_id,node_id,worker,kind,tool,tool_use_id,is_error,summary,detail,metadata) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb) RETURNING id',
                            (scope.exploration_id,node,worker,'tool_use' if state=='started' else 'tool_result',action.name,call_id,is_error,detail.split('\n',1)[0][:200],detail,metadata)))[0]
                        if state=='started':
                            tx.execute(Query("INSERT INTO artex_tool_calls(call_key,task_id,exploration_id,run_id,session_id,turn_id,tool_call_id,tool,agent,use_activity_id,status) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'started')",(key,scope.task_id,scope.exploration_id,scope.run_id,session,turn,call_id,action.name,role,activity)))
                            tx.execute(Query('INSERT INTO tool_usage(tool_key,agent_key,task_id,exploration_id,intent_id,session_id) VALUES($1,$2,$3,$4,$5,$6)',(action.name,role,scope.task_id,scope.exploration_id,node,session)))
                        else:tx.execute(Query('UPDATE artex_tool_calls SET result_activity_id=$2,status=$3,completed_at=now() WHERE call_key=$1',(key,activity,state)))
            except EffectUncertain:raise
            except Exception:raise EffectUncertain('Tool ledger persistence unconfirmed') from None
        return record
