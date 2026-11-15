"""Inert actual GoalPlanner/journal integration; SQL is simulated, not executed."""
import unittest,tempfile
from contextlib import contextmanager
from copy import deepcopy
from artex.agent.bootstrap import *
from artex.agent.goals import GoalPlanner
from artex.agent.chat import ChatBinding
from artex.agent.runtime import Grant,Limits
from artex.db.exploration import ExplorationStore
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
from artex.manager import CancellationToken
from test_planner_cycle import GraphDB
from test_goal_planner import goals
from test_chat_runtime import Provider,MODEL,text

class DB(GraphDB):
    def __init__(self):super().__init__();self.rows=self.rows[:1];self.receipt=None;self.description='background';self.goal='explicit outcome';self.fail_finish=False
    @contextmanager
    def transaction(self):
        old=deepcopy((self.rows,self.links,self.receipt))
        try:yield self
        except BaseException:self.rows,self.links,self.receipt=old;raise
    def fetch_all(self,q):
        if q.sql.startswith('SELECT description,goal'):return [(self.description,self.goal)]
        if q.sql.startswith('SELECT task_id,exploration_id,run_id'):return [] if self.receipt is None else [self.receipt]
        if "kind='fact' AND state='origin' ORDER" in q.sql:return [(1,)]
        if "kind IN ('goal','intent')" in q.sql:return [(r[0],) for r in self.rows if r[1] in ('goal','intent')][:1]
        if q.sql.startswith('SELECT n.id, EXISTS'):return [(r[0],(9,1,'spawns',r[0]) in self.links) for r in self.rows if r[1]=='goal']
        if q.sql.startswith('UPDATE public.artex_task_bootstrap'):
            if self.fail_finish:raise RuntimeError('private DB failure')
            ids,task,run,owner,rev,exp,request,digest=q.params
            if (self.receipt[1],self.receipt[3],self.receipt[4])!=(exp,request,digest) or self.receipt[2]!=run or self.receipt[5]!=owner or self.receipt[6]!=rev or self.receipt[7]!='running':return []
            self.receipt=(*self.receipt[:6],rev+1,'prepared',ids);return [self.receipt]
        return super().fetch_all(q)
    def fetch_one(self,q):
        if q.sql.startswith('INSERT INTO public.artex_task_bootstrap'):
            task,exp,request,digest,owner=q.params;self.receipt=(task,exp,71,request,digest,owner,1,'running',[]);return self.receipt
        return super().fetch_one(q)
    def execute(self,q):
        if q.sql.startswith('UPDATE public.artex_task_bootstrap'):
            task,run,owner,rev=q.params
            if self.receipt[2:3]!=(run,) or self.receipt[5]!=owner or self.receipt[6]!=rev or self.receipt[7]!='running':return 0
            self.receipt=(*self.receipt[:6],rev+1,'unresolved',[]);return 1
        return super().execute(q)

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=DB();self.journal=BootstrapJournal(self.db);self.provider=Provider([goals(),text('submitted')]);self.calls=[]
        self.transcript=ChatTranscriptAdapter(TranscriptStore(self.tmp.name));self.valid=True
        @contextmanager
        def guard(*args):yield
        self.guard=guard
        def factory(scope,current,lifetime):
            self.calls.append(scope)
            return GoalPlanner(store=ExplorationStore(self.db,9),transcript=self.transcript,current=current,lifecycle_guard=lifetime,
                provider_resolver=lambda s,m:ChatBinding(m,self.provider),authorize=lambda *a:Grant(True,True,True),validate_input=lambda *a:True,limits=Limits(max_turns=8))
        self.coordinator=BootstrapCoordinator(self.journal,planner_factory=factory,lifecycle_guard=guard,current=lambda *a:self.valid)
    def run_bootstrap(self,**kwargs):
        return self.coordinator.run(5,9,'request',model=MODEL,system='explicit goals only',policy_revision='host-policy-v1',token=CancellationToken(),**kwargs)
    def test_actual_goal_planner_prepares_without_admitting_task(self):
        receipt=self.run_bootstrap()
        self.assertEqual(receipt.phase,'prepared');self.assertEqual(receipt.run_id,71);self.assertEqual(receipt.goal_ids,(2,))
        self.assertEqual(self.calls[0].run_id,71);self.assertNotIn(receipt.owner_token,repr(receipt))
        self.assertFalse(any(q.sql.startswith('UPDATE tasks') for q in self.db.queries))
    def test_prepared_same_request_replay_never_calls_provider(self):
        first=self.run_bootstrap();count=len(self.provider.calls)
        self.assertEqual(self.run_bootstrap(),first);self.assertEqual(len(self.provider.calls),count)
    def test_changed_task_input_rejects_receipt(self):
        self.run_bootstrap();self.db.goal='changed'
        with self.assertRaises(BootstrapConflict):self.run_bootstrap()
    def test_pending_owner_never_stolen(self):
        self.journal.claim(5,9,'request','a'*64,'b'*64)
        with self.assertRaises(BootstrapConflict):self.run_bootstrap()
        self.assertEqual(self.provider.calls,[])
    def test_text_only_is_not_initialization_success(self):
        self.provider.responses=[text('I created goals')]
        with self.assertRaises(BootstrapUnresolved):self.run_bootstrap()
        self.assertEqual(self.db.receipt[7],'unresolved')
    def test_finish_uncertainty_never_replays_effects(self):
        self.db.fail_finish=True
        with self.assertRaises(BootstrapUnresolved):self.run_bootstrap()
        count=len(self.provider.calls)
        with self.assertRaises(BootstrapConflict):self.run_bootstrap()
        self.assertEqual(len(self.provider.calls),count)
    def test_stale_authority_before_claim_has_no_effect(self):
        self.valid=False
        with self.assertRaises(BootstrapConflict):self.run_bootstrap()
        self.assertIsNone(self.db.receipt)
    def test_existing_goals_refuse_duplicate_bootstrap(self):
        self.db.rows.append((2,'goal','{}',0,'open','goals','','','',None))
        with self.assertRaises(BootstrapConflict):self.run_bootstrap()
        self.assertIsNone(self.db.receipt)
    def test_changed_policy_rejects_prepared_receipt(self):
        self.run_bootstrap()
        with self.assertRaises(BootstrapConflict):
            self.coordinator.run(5,9,'request',model=MODEL,system='explicit goals only',policy_revision='host-policy-v2',token=CancellationToken())
    def test_foreign_planner_rejected_after_claim_without_provider(self):
        original=self.coordinator.factory
        def factory(*args):
            planner=original(*args);planner.store.executor=DB();return planner
        self.coordinator.factory=factory
        with self.assertRaises(BootstrapUnresolved):self.run_bootstrap()
        self.assertEqual(self.provider.calls,[]);self.assertEqual(self.db.receipt[7],'unresolved')

    def test_factory_cannot_execute_before_coordinator_admission(self):
        factory=self.coordinator.factory;errors=[]
        def early(scope,current,guard):
            planner=factory(scope,current,guard)
            try:planner.run(scope,'early','different goal',token=CancellationToken())
            except Exception as exc:errors.append(exc)
            return planner
        self.coordinator.factory=early
        self.run_bootstrap();self.assertEqual(len(errors),1)
        self.assertEqual(len(self.provider.calls),2)
    def test_retained_callback_rejects_foreign_thread(self):
        from threading import Thread
        factory=self.coordinator.factory;errors=[]
        def capturing(scope,current,guard):
            planner=factory(scope,current,guard)
            def hook():
                def foreign():
                    try:current(scope)
                    except Exception as exc:errors.append(exc)
                thread=Thread(target=foreign);thread.start();thread.join(1)
            self.provider.hook=hook
            return planner
        self.coordinator.factory=capturing
        self.run_bootstrap();self.assertTrue(errors)
    def test_authority_loss_during_finish_rolls_back_prepared(self):
        fetch=self.db.fetch_all
        def invalidate(query):
            result=fetch(query)
            if query.sql.startswith('UPDATE public.artex_task_bootstrap'):self.valid=False
            return result
        self.db.fetch_all=invalidate
        with self.assertRaises(BootstrapUnresolved):self.run_bootstrap()
        self.assertEqual(self.db.receipt[7],'unresolved')

class SchemaFixture:
    def __init__(self):self.scripts=[];self.identity=('fixture',160000,'off','on',0);self.objects=(None,None,'tasks','explorations')
    @contextmanager
    def transaction(self):
        old=list(self.scripts)
        try:yield self
        except BaseException:self.scripts=old;raise
    def fetch_one(self,q):
        if 'pg_advisory' in q.sql:return (None,)
        if q.sql==IDENTITY_SQL:return self.identity
        return self.objects
    def execute_script(self,sql):self.scripts.append(sql)

class JournalInstallerTests(unittest.TestCase):
    def test_explicit_fresh_install_only(self):
        db=SchemaFixture();install_journal(db,expected_database='fixture',expected_schema='public',allow_create=True)
        self.assertEqual(db.scripts[-1],DDL);self.assertNotIn('IF NOT EXISTS',DDL)
    def test_existing_objects_rejected_not_assumed_compatible(self):
        for objects in [('artex_task_bootstrap',None,'tasks','explorations'),(None,'sequence','tasks','explorations'),(None,None,None,'explorations')]:
            with self.subTest(objects=objects):
                db=SchemaFixture();db.objects=objects
                with self.assertRaises(SchemaRefused):install_journal(db,expected_database='fixture',expected_schema='public',allow_create=True)
                self.assertEqual(db.scripts,[])
    def test_wrong_database_or_implicit_creation_has_no_ddl(self):
        db=SchemaFixture()
        with self.assertRaises(SchemaRefused):install_journal(db,expected_database='other',expected_schema='public',allow_create=True)
        with self.assertRaises(SchemaRefused):install_journal(db,expected_database='fixture',expected_schema='public')
        self.assertEqual(db.scripts,[])
