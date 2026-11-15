import json,unittest
from contextlib import nullcontext
from datetime import datetime,timezone
from artex.tools.graph import *
from artex.agent.runtime import Grant
from test_conversation_activity import TransactionFake
from test_process import Context
NOW=datetime.now(timezone.utc)
def node(id,kind,payload=None,state='open'):return (id,kind,payload or {'summary':'fixture'},0,state,'','','','',NOW)
class GraphToolsTests(unittest.TestCase):
    def make(self,rows,intent=0):
        self.db=TransactionFake(rows);self.valid=True;self.actions=[]
        self.bridge=GraphTools(ExplorationStore(self.db,9),GraphScope(3,9,1,'rev','worker',intent),current=lambda s:self.valid,lifecycle_guard=lambda s:nullcontext(),authorize=lambda c,a:(self.actions.append(a.arguments()) or Grant(True,True,True)),asset_authorize=lambda *a:Grant(True,True,True))
        return {t.name:t for t in self.bridge.tools()}
    def test_record_fact_real_store_source_payload_and_lineage(self):
        tools=self.make([[(3,)],[node(7,'intent')],(8,),1,1],intent=7)
        result=tools['record_fact'].callback(Context(),{'summary':'observed','evidence':'line','confidence':'observed','asset_ids':[5]})
        self.assertEqual(result,'fact recorded: 8')
        insert=next(q for _,q in self.db.calls if q.sql.startswith('INSERT INTO exploration_nodes'))
        self.assertEqual(insert.params[:2],(9,'fact'));self.assertEqual(insert.params[3:],(5,'confirmed','worker'))
        self.assertEqual(json.loads(insert.params[2]),{'summary':'observed','evidence':'line','confidence':'observed'})
        self.assertEqual(self.db.calls[-1][1].params,(9,7,'yields',8))
    def test_invalid_worker_intent_or_denied_grant_no_db(self):
        tools=self.make([],intent=7)
        with self.assertRaises(GraphToolDenied):tools['record_fact'].callback(Context(),{'summary':'x','intent_id':8})
        self.bridge.authorize=lambda *a:Grant(True,False,True)
        with self.assertRaises(GraphToolDenied):tools['list_goals'].callback(Context(),{})
        self.assertEqual(self.db.calls,[])
    def test_batch_lineage_failure_rolls_back_outer_transaction(self):
        tools=self.make([[(3,)],[node(7,'intent')],(8,),1,[node(7,'intent')],(9,),0],intent=7)
        with self.assertRaises(GraphToolDenied):tools['record_fact'].callback(Context(),{'facts':[{'summary':'a'},{'summary':'b'}]})
        self.assertEqual(self.db.events[-1],'rollback')
    def test_proof_requires_local_correct_kind_and_persists_link_then_state(self):
        tools=self.make([[(3,)],[node(2,'goal')],[node(4,'fact')],1,1])
        result=tools['prove_goal'].callback(Context(),{'goal_id':2,'evidence_id':4,'reason':'authorized fixture'})
        self.assertEqual(result,'goal 2 marked met')
        queries=[q for _,q in self.db.calls]
        self.assertEqual(queries[-2].params,(9,4,'proves',2));self.assertEqual(queries[-1].params[:3],('met',2,9))
        self.assertFalse(any('UPDATE tasks' in q.sql for q in queries))
    def test_foreign_evidence_no_goal_update(self):
        tools=self.make([[(3,)],[node(2,'goal')],[]])
        with self.assertRaises(GraphToolDenied):tools['prove_goal'].callback(Context(),{'goal_id':2,'evidence_id':4})
        self.assertFalse(any(q.sql.startswith('UPDATE') for _,q in self.db.calls));self.assertEqual(self.db.events[-1],'rollback')
    def test_duplicate_proof_must_verify_existing_link(self):
        tools=self.make([[(3,)],[node(2,'goal')],[node(4,'finding')],0,[(1,)],1])
        self.assertIn('marked met',tools['prove_goal'].callback(Context(),{'goal_id':2,'evidence_id':4}))
        self.assertIn("rel='proves'",self.db.calls[-2][1].sql)
    def test_list_local_pagination_truncation_and_no_raw_detail(self):
        tools=self.make([[(3,)],(2,),[node(5,'fact',{'summary':'x'*161,'detail':'hidden'}),node(4,'fact')]])
        result=json.loads(tools['list_facts'].callback(Context(),{'limit':1,'q':'fixture'}))
        self.assertEqual(result['next_before'],5);self.assertTrue(result['has_more']);self.assertTrue(result['facts'][0]['summary_truncated'])
        self.assertNotIn('hidden',json.dumps(result));self.assertEqual(self.db.calls[-1][1].params,(9,'fixture',0,2))
    def test_lifecycle_change_during_approval_blocks_sql(self):
        tools=self.make([])
        def authorize(c,a):self.valid=False;return Grant(True,True,True)
        self.bridge.authorize=authorize
        with self.assertRaises(GraphToolDenied):tools['list_goals'].callback(Context(),{})
        self.assertEqual(self.db.calls,[])
