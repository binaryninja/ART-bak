import unittest,tempfile,json
from contextlib import contextmanager,nullcontext
from copy import deepcopy
from artex.agent.activity_projection import *
from artex.agent.engine import EngineScope
from artex.agent.planner import PlannerScope
from artex.agent.runtime import RunResult,Grant
from artex.agent.chat import ChatOutcome
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
from artex.sidequestion import Model

class DB:
    def __init__(self):self.rows=[];self.fail=False;self.hook=None
    @contextmanager
    def transaction(self):
        before=deepcopy(self.rows)
        try:yield self
        except BaseException:self.rows=before;raise
    def fetch_one(self,q):
        if q.sql.startswith('SELECT exploration_id'):return (9,None)
        if q.sql.startswith('INSERT INTO activity'):
            if self.fail:raise RuntimeError('private failure')
            marker=json.loads(q.params[9])['_artex_projection'];self.rows.append((len(self.rows)+1,marker,q.params));return (len(self.rows),)
        raise AssertionError(q.sql)
    def fetch_all(self,q):
        if q.sql.startswith("SELECT id,"):
            if self.hook:self.hook()
            return [(r[0],r[1]==json.loads(q.params[2]) and tuple(r[2][1:9])+tuple(r[2][10:])==tuple(q.params[3:])) for r in self.rows if r[1]['key']==q.params[1]]
        if q.sql.startswith('SELECT state FROM task_archives'):return []
        if q.sql.startswith('SELECT id FROM exploration_nodes'):return [(7,)]
        raise AssertionError(q.sql)

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.t=ChatTranscriptAdapter(TranscriptStore(self.tmp.name));self.db=DB();self.live=True;self.allowed=True
        self.scope=EngineScope(5,9,71,'r1');self.s=PlannerScope(5,9,71,'r1',Model('p','m','i'),'explicit')
        self.identity=self.s.identity();self.user={'role':'user','content':[{'type':'text','text':'wake'}]}
        self.turn=self.t.begin_scoped(self.s.session_id,'one',self.user,self.identity)
        self.result=RunResult('completed',[self.user,{'role':'assistant','content':[{'type':'text','text':'observed result'}]}],{'input_tokens':5,'output_tokens':2},1,0)
        self.t.commit(self.s.session_id,self.turn,self.result)
        self.outcome=ChatOutcome(self.turn,self.result,True)
        self.p=ActivityProjector(self.db,self.scope,transcript=self.t,current=lambda s:self.live,lifecycle_guard=lambda s:nullcontext(),authorize=lambda p:Grant(self.allowed,True,True))
    def project(self):return self.p.project('planner',self.s,self.outcome,self.identity)
    def test_confirmed_projection_idempotent_and_real_usage(self):
        self.assertEqual(self.project(),(1,));self.assertEqual(self.project(),(1,));self.assertEqual(len(self.db.rows),1)
        params=self.db.rows[0][2];self.assertEqual(params[3],'result');self.assertEqual(params[10:12],(5,2))
    def test_unconfirmed_or_foreign_identity_no_rows(self):
        self.outcome=ChatOutcome(self.turn,self.result,False)
        with self.assertRaises(ProjectionUnavailable):self.project()
        self.assertEqual(self.db.rows,[])
    def test_final_grant_revocation_no_rows(self):
        self.db.hook=lambda:setattr(self,'allowed',False)
        with self.assertRaises(ProjectionUnavailable):self.project()
        self.assertEqual(self.db.rows,[])
    def test_failed_insert_unknown_not_execution_retry(self):
        self.db.fail=True
        with self.assertRaises(ProjectionUncertain) as err:self.project()
        self.assertNotIn('private',str(err.exception));self.assertEqual(self.db.rows,[])
    def test_canonical_result_not_caller_supplied_text(self):
        self.outcome=ChatOutcome(self.turn,RunResult('completed',[],{},1,0),True)
        self.project();self.assertEqual(self.db.rows[0][2][8],'observed result')
    def test_conflicting_existing_receipt_refused(self):
        self.project();self.db.rows[0][1]['digest']='bad'
        with self.assertRaises(ProjectionUnavailable):self.project()
        self.assertEqual(len(self.db.rows),1)

    def test_empty_second_turn_never_repeats_prior_output(self):
        self.project()
        user={'role':'user','content':[{'type':'text','text':'next wake'}]}
        turn=self.t.begin_scoped(self.s.session_id,'two',user,self.identity)
        result=RunResult('completed',turn.messages,{},1,0)
        self.t.commit(self.s.session_id,turn,result)
        self.outcome=ChatOutcome(turn,result,True)
        self.project();self.assertEqual(self.db.rows[-1][2][8],'')

    def test_preserved_marker_cannot_bless_edited_payload(self):
        self.project()
        row=list(self.db.rows[0][2]);row[8]='edited text'
        self.db.rows[0]=(self.db.rows[0][0],self.db.rows[0][1],tuple(row))
        with self.assertRaises(ProjectionUnavailable):self.project()
        self.assertEqual(len(self.db.rows),1)
