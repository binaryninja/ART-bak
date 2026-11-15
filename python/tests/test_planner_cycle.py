import json,tempfile,unittest
from contextlib import contextmanager
from dataclasses import replace
from copy import deepcopy
from datetime import datetime,timezone
from artex.agent.planner import PlannerCycle,PlannerScope
from artex.agent.chat import ChatBinding,ChatPersistenceError
from artex.agent.runtime import Grant
from artex.db.exploration import ExplorationStore
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
from test_chat_runtime import Provider,MODEL,text

NOW=datetime.now(timezone.utc)
def node(id,kind,payload,state):return (id,kind,json.dumps(payload),0,state,'planner','','','',NOW)
class GraphDB:
    def __init__(self):self.rows=[node(1,'fact',{'summary':'origin'},'origin'),node(2,'goal',{'summary':'fixture goal'},'open')];self.queries=[];self.links=[];self.live=True
    @contextmanager
    def transaction(self):
        old=deepcopy((self.rows,self.links))
        try:yield self
        except BaseException:self.rows,self.links=old;raise
    def fetch_one(self,q):
        self.queries.append(q)
        if q.sql.startswith('SELECT id FROM tasks'):return (5,) if self.live else None
        if q.sql.startswith('INSERT INTO exploration_nodes'):
            id=max(r[0] for r in self.rows)+1;self.rows.append((id,q.params[1],q.params[2],q.params[3],q.params[4],q.params[5],'','','',NOW));return (id,)
        raise AssertionError(q.sql)
    def fetch_all(self,q):
        self.queries.append(q)
        if q.sql.startswith('SELECT id FROM tasks'):return [(5,)]
        if "kind='fact' AND state='origin'" in q.sql:return [(1,)]
        if 'WHERE id=$1 AND exploration_id=$2' in q.sql:return [r for r in self.rows if r[0]==q.params[0]]
        if 'FROM exploration_nodes' in q.sql:return self.rows[:q.params[-1]]
        raise AssertionError(q.sql)
    def execute(self,q):self.queries.append(q);self.links.append(q.params);return 1

def intent(summary='inert direction'):
    return ({'role':'assistant','content':[{'type':'tool_use','id':'c1','name':'add_intent','input':{'summary':summary}}]},'tool_use',{})

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=GraphDB();self.graph=ExplorationStore(self.db,9);self.store=TranscriptStore(self.temp.name);self.tx=ChatTranscriptAdapter(self.store)
        self.scope=PlannerScope(5,9,1,'r1',MODEL,'Explicit inert planner instructions; no prove_goal capability.')
        self.valid=True;self.provider=Provider([intent(),text('queued')])
        @contextmanager
        def guard(scope):yield
        self.core=PlannerCycle(store=self.graph,transcript=self.tx,current=lambda scope:self.valid and scope==self.scope,lifecycle_guard=guard,
                               provider_resolver=lambda scope,model:ChatBinding(model,self.provider),authorize=lambda *a:Grant(True,True,True),asset_authorize=lambda *a:Grant(True,True,True))
    def test_actual_cycle_creates_durable_intent_and_lineage(self):
        wake=self.core.prepare(self.scope,'cycle1');result=self.core.run(wake)
        self.assertTrue(result.outcome.persisted);self.assertTrue(result.active_intents);self.assertFalse(result.goals_met);self.assertFalse(result.stalled)
        self.assertEqual(self.db.rows[-1][1],'intent');self.assertEqual(self.db.links[-1],(9,1,'derived_from',3))
        self.assertEqual(len(self.store.read('exp9-planner')),4)
        self.assertEqual(self.provider.calls[-1]['messages'][-1]['content'][0]['tool_use_id'],'c1')
    def test_replay_same_wake_never_duplicates_tool(self):
        wake=self.core.prepare(self.scope,'cycle');self.core.run(wake);again=self.core.run(wake)
        self.assertTrue(again.outcome.reused);self.assertEqual(len(self.provider.calls),2);self.assertEqual(len(self.db.rows),3)
    def test_fresh_context_accumulating_raw_log(self):
        self.provider.responses=[text('round1'),text('round2')]
        self.core.run(self.core.prepare(self.scope,'one'));self.core.run(self.core.prepare(self.scope,'two'))
        self.assertEqual(len(self.provider.calls[1]['messages']),1);self.assertEqual(len(self.store.read('exp9-planner')),4)
    def test_changed_wake_same_cycle_fails_without_effect_replay(self):
        wake=self.core.prepare(self.scope,'cycle');self.core.run(wake)
        with self.assertRaises(ChatPersistenceError):self.core.run(self.core.prepare(self.scope,'cycle'))
        self.assertEqual(len(self.provider.calls),2);self.assertEqual(len(self.db.rows),3)
    def test_model_claim_not_goal_completion(self):
        self.provider.responses=[text('All goals done!')]
        result=self.core.run(self.core.prepare(self.scope,'cycle'))
        self.assertFalse(result.goals_met);self.assertTrue(result.stalled)
    def test_denied_target_never_persists_intent(self):
        self.core.asset_authorize=lambda *a:Grant(True,False,True)
        result=self.core.run(self.core.prepare(self.scope,'cycle'))
        self.assertEqual(len(self.db.rows),2);self.assertFalse(result.goals_met)
    def test_pause_parent_guard_prevents_intent(self):
        wake=self.core.prepare(self.scope,'cycle');self.db.live=False
        result=self.core.run(wake);self.assertEqual(len(self.db.rows),2);self.assertFalse(result.active_intents)
    def test_cancel_inflight_persists_cancelled_cycle(self):
        self.provider.responses=[text('partial')];self.provider.hook=lambda:self.core.cancel(5)
        result=self.core.run(self.core.prepare(self.scope,'cycle'))
        self.assertEqual(result.outcome.result.reason,'cancelled');self.assertTrue(result.outcome.persisted)
    def test_revision_change_blocks_terminal_commit_and_replay(self):
        self.provider.responses=[text('partial')];self.provider.hook=lambda:setattr(self,'valid',False)
        wake=self.core.prepare(self.scope,'cycle')
        with self.assertRaises(ChatPersistenceError):self.core.run(wake)
        self.valid=True
        with self.assertRaises(ChatPersistenceError):self.core.run(wake)
        self.assertEqual(len(self.provider.calls),1)

    def restart(self):
        self.core=PlannerCycle(store=ExplorationStore(self.db,9),
            transcript=ChatTranscriptAdapter(TranscriptStore(self.temp.name)),
            current=lambda scope:self.valid and scope==self.scope,lifecycle_guard=self.core.guard,
            provider_resolver=lambda scope,model:ChatBinding(model,self.provider),
            authorize=lambda *a:Grant(True,True,True),asset_authorize=lambda *a:Grant(True,True,True))

    def test_restart_same_revision_changed_prompt_replay_denied(self):
        self.provider.responses=[text('first')]
        wake=self.core.prepare(self.scope,'cycle');self.core.run(wake)
        self.scope=replace(self.scope,system='Different explicit planner instructions')
        self.restart()
        changed=replace(wake,scope=self.scope)
        with self.assertRaises(ChatPersistenceError):self.core.run(changed)
        self.assertEqual(len(self.provider.calls),1)
    def test_restart_same_revision_changed_model_fields_replay_denied(self):
        self.provider.responses=[text('first')]
        wake=self.core.prepare(self.scope,'cycle');self.core.run(wake)
        self.scope=replace(self.scope,model=replace(MODEL,model='different-model'))
        self.restart()
        with self.assertRaises(ChatPersistenceError):self.core.run(replace(wake,scope=self.scope))
        self.assertEqual(len(self.provider.calls),1)
