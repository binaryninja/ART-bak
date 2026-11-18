"""Real manager/host + goals journal + engine composition with inert adapters."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from threading import Event,Thread
from artex.agent.bootstrap import BootstrapCoordinator,BootstrapJournal,BootstrapConflict,BootstrapUnresolved
from artex.agent.bootstrap_handle import BootstrapHandle
from artex.agent.goals import GoalPlanner
from artex.agent.engine import TaskEngineHandle
from artex.agent.host import AgentHost
from artex.agent.chat import ChatBinding
from artex.agent.runtime import Grant
from artex.db.tasks import TaskStore
from artex.manager import TaskManager,TaskConflict
import test_bootstrap as boot
import test_engine as engine
import test_engine_runner_review as hosted
import test_manager as manager_fixture
from test_goal_planner import goals
from test_chat_runtime import Provider,MODEL,text

class DB(hosted.SharedDB):
    def __init__(self):
        super().__init__();self.rows=self.rows[:1];self.receipt=None;self.fail_finish=False
        row=list(self.row);row[7]='created';row[8]=False;self.row=tuple(row)
    @contextmanager
    def transaction(self):
        old=deepcopy((self.row,self.rows,self.links,self.receipt))
        try:yield self
        except BaseException:self.row,self.rows,self.links,self.receipt=old;raise
    def fetch_one(self,q):
        if q.sql.startswith('INSERT INTO public.artex_task_bootstrap'):return boot.DB.fetch_one(self,q)
        return super().fetch_one(q)
    def fetch_all(self,q):
        if q.sql.startswith('SELECT description,goal'):
            required='running' if "status='running'" in q.sql else 'created'
            return [(self.row[4],self.row[5])] if self.row[7]==required and not self.row[8] else []
        if q.sql.startswith(('SELECT task_id,exploration_id,run_id','UPDATE public.artex_task_bootstrap','SELECT n.id, EXISTS')) or "kind='fact' AND state='origin' ORDER" in q.sql or "kind IN ('goal','intent')" in q.sql:
            return boot.DB.fetch_all(self,q)
        return super().fetch_all(q)
    def execute(self,q):
        if q.sql.startswith('UPDATE public.artex_task_bootstrap'):return boot.DB.execute(self,q)
        return super().execute(q)

class Runner:
    def __init__(self):self.items=[];self.factory=None
    def ready(self,task):return True
    def validate_create(self,*args):return None
    def start(self,task,token):
        h=self.factory(task,token);self.items.append(h);return h
    def handles(self):return tuple(self.items)
    def settle_completed(self,complete):
        done=[]
        for h in tuple(self.items):
            if h.unresolved:continue
            if h.token.cancelled:
                if h.wait(0):self.items.remove(h)
            elif h.settle_completed(complete):self.items.remove(h);done.append(h.task_id)
        return tuple(done)

class BootstrapHandleTests(unittest.TestCase):
    def setUp(self):
        self.f=engine.EngineTests();self.f.database_factory=DB;self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.db=self.f.db;self.runner=Runner()
        self.manager=TaskManager(TaskStore(self.db),runner=self.runner,dto_enricher=manager_fixture.enrich)
        self.manager.load_existing();self.host=AgentHost(self.manager,self.runner)
        self.provider=Provider([goals(),text('seeded')]);self.child_calls=[]
        def planner(scope,current,guard):
            return GoalPlanner(store=self.f.graph,transcript=self.f.planner.transcript,current=current,lifecycle_guard=guard,
                provider_resolver=lambda s,m:ChatBinding(m,self.provider),authorize=lambda *a:Grant(True,True,True),validate_input=lambda *a:True)
        self.coordinator=BootstrapCoordinator(BootstrapJournal(self.db),planner_factory=planner,lifecycle_guard=self.f.guard,current=lambda *a:self.f.valid)
        def child(task,token,scope,current,guard):
            self.child_calls.append(scope);self.f.token=token;self.f.ps=replace(self.f.ps,run_id=scope.run_id)
            return TaskEngineHandle(scope,token,store=self.f.graph,planner=self.f.planner,planner_scope=self.f.ps,
                worker=self.f.worker,worker_model=MODEL,worker_system='inert worker',owner='fixture',current=current,lifecycle_guard=guard,single_process=True)
        self.child_factory=child
        self.runner.factory=lambda task,token:BootstrapHandle(task,token,coordinator=self.coordinator,manager=self.manager,model=MODEL,system='explicit goals',policy_revision='r1',request_id='bootstrap-request',engine_factory=self.child_factory)
    def start(self):
        self.host.start('5');return self.runner.items[0]
    def advance(self,h,t=0):return self.host.advance_owned(lambda:h.advance(t)).value
    def test_installed_before_journal_then_actual_engine_on_next_tick(self):
        h=self.start();self.assertIsNone(self.db.receipt);self.assertEqual(self.provider.calls,[])
        self.assertFalse(h.settle_completed(self.manager.complete))
        self.assertEqual(self.advance(h).action,'bootstrap_prepared');self.assertEqual(h.scope.run_id,71)
        self.assertEqual(self.f.pp.calls,[]);self.assertEqual(self.host.poll().settled,())
        self.assertEqual(self.advance(h,1).action,'planner')
        self.assertEqual(self.advance(h,2).action,'worker')
        self.f.met();self.assertEqual(self.host.poll().settled,('5',));self.assertEqual(self.manager.status('5'),'done')
    def test_early_direct_handle_has_no_generation_authority(self):
        from artex.manager import CancellationToken
        row=self.manager.lifecycle_snapshot('5');h=self.runner.factory(row,CancellationToken())
        with self.assertRaises(BootstrapConflict):h.advance(0)
        self.assertIsNone(self.db.receipt);self.assertEqual(self.provider.calls,[])
    def test_created_coordinator_cannot_run_after_manager_admission(self):
        self.start()
        from artex.manager import CancellationToken
        with self.assertRaises(BootstrapConflict):self.coordinator.run(5,9,'other',model=MODEL,system='goals',policy_revision='r1',token=CancellationToken())
        self.assertIsNone(self.db.receipt)
    def test_cancel_before_first_tick_is_clean_and_no_journal(self):
        h=self.start();self.host.pause('5');self.assertTrue(h.wait(0));self.assertFalse(h.unresolved)
        self.assertIsNone(self.db.receipt);self.assertEqual(self.provider.calls,[])
    def test_cancel_during_provider_drains_retains_unresolved(self):
        h=self.start();entered=Event();release=Event();original=self.provider.complete;errors=[]
        def blocked(*a):entered.set();release.wait(2);return original(*a)
        self.provider.complete=blocked
        def step():
            try:self.advance(h)
            except Exception as exc:errors.append(exc)
        thread=Thread(target=step);thread.start();self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(TaskConflict):self.host.pause('5',timeout=0)
            self.assertFalse(h.wait(0))
        finally:release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertTrue(h.unresolved);self.assertTrue(errors)
        self.assertEqual(self.host.poll().settled,());self.assertIn(h,self.runner.items)
    def test_child_execution_does_not_begin_in_factory(self):
        factory=self.child_factory;errors=[]
        def attempting(*a):
            child=factory(*a)
            try:child.advance(0)
            except Exception as exc:errors.append(exc)
            return child
        self.child_factory=attempting;h=self.start()
        # Violating the inert factory contract quarantines the child rather than
        # silently treating its failed attempted execution as clean construction.
        self.advance(h);self.assertTrue(errors);self.assertEqual(self.f.pp.calls,[])
    def test_duplicate_advance_rejected_during_bootstrap(self):
        h=self.start();seen=[]
        def reenter():
            try:h.advance(0)
            except BootstrapConflict:seen.append(True)
        self.provider.hook=reenter
        self.advance(h);self.assertTrue(seen);self.assertEqual(len(self.child_calls),1)

    def test_original_generation_retained_through_outer_guard_exit(self):
        at_exit, release = Event(), Event()
        depth = 0
        @contextmanager
        def guard(*ids):
            nonlocal depth
            depth += 1
            try:
                yield
            finally:
                depth -= 1
                if depth == 0 and self.runner.items and self.runner.items[0].phase == 'engine':
                    at_exit.set()
                    self.assertTrue(release.wait(3))
        self.coordinator.guard = guard
        handle = self.start()
        with ThreadPoolExecutor(max_workers=1) as pool:
            for expected in ('bootstrap_prepared', 'idle'):
                if expected == 'idle':
                    self.f.met()
                at_exit.clear()
                release.clear()
                step = pool.submit(self.advance, handle)
                try:
                    self.assertTrue(at_exit.wait(2))
                    self.assertEqual(handle.phase, 'engine')
                    self.assertFalse(self.manager.complete('5', handle.token))
                    self.assertTrue(self.manager.owns_generation('5', handle.token, handle))
                    self.assertFalse(handle.wait(0))
                    self.assertFalse(self.host.drain(0).all_settled)
                finally:
                    release.set()
                self.assertEqual(step.result(2).action, expected)
            at_exit.clear()
            release.clear()
            poll = pool.submit(self.host.poll)
            try:
                self.assertTrue(at_exit.wait(2))
                status, _, retained, _ = self.manager.retirement_snapshot('5')
                self.assertEqual(status, 'done')
                self.assertTrue(retained)
                self.assertFalse(self.host.drain(0).all_settled)
            finally:
                release.set()
            self.assertTrue(poll.result(2).all_settled)
        self.assertFalse(self.manager.retirement_snapshot('5')[2])

    def test_replaced_owner_guard_refused_before_work(self):
        handle = self.start()
        self.coordinator.guard = lambda *ids: self.fail('replacement guard entered')
        with self.assertRaisesRegex(BootstrapConflict, 'lifecycle guard changed'):
            self.advance(handle)
        self.assertIsNone(self.db.receipt)
        self.assertEqual(self.provider.calls, [])

    def test_owner_guard_exit_failure_fences_original_generation(self):
        depth = 0
        @contextmanager
        def guard(*ids):
            nonlocal depth
            depth += 1
            try:
                yield
            finally:
                depth -= 1
                if depth == 0:
                    raise OSError('owner release uncertain')
        self.coordinator.guard = guard
        handle = self.start()
        with self.assertRaises(BootstrapUnresolved):
            self.advance(handle)
        self.assertFalse(self.manager.owns_generation('5', handle.token, handle, allow_cancelled=True))
        self.assertFalse(self.manager.complete('5', handle.token))
        self.assertTrue(self.manager.retirement_snapshot('5')[2])
        self.assertFalse(self.host.drain(0).all_settled)
    def test_foreign_engine_scope_retained_unresolved(self):
        factory=self.child_factory
        def changed(*a):
            child=factory(*a);child.scope=replace(child.scope,run_id=999);return child
        self.child_factory=changed;h=self.start()
        with self.assertRaises(BootstrapUnresolved):self.advance(h)
        self.assertTrue(h.unresolved);self.assertIsNotNone(h._child)

    def test_child_cannot_advance_outside_outer_owned_operation(self):
        h=self.start();self.advance(h);before=len(self.f.pp.calls)
        with self.assertRaises(Exception):h._child.advance(1)
        self.assertEqual(len(self.f.pp.calls),before)
    def test_cancel_during_child_construction_retains_then_drains_child(self):
        factory=self.child_factory
        def cancel(task,token,*args):
            token.cancel('fixture pause');return factory(task,token,*args)
        self.child_factory=cancel;h=self.start();progress=self.advance(h)
        self.assertTrue(progress.cancelled);self.assertIsNotNone(h._child);self.assertTrue(h.wait(0))
        self.assertEqual(self.f.pp.calls,[]);self.assertFalse(h.unresolved)
    def test_claim_commit_cancellation_retains_unresolved_no_models(self):
        h=self.start();original=self.db.fetch_one
        def cancel(query):
            row=original(query)
            if query.sql.startswith('INSERT INTO public.artex_task_bootstrap'):h.token.cancel('fixture pause')
            return row
        self.db.fetch_one=cancel
        with self.assertRaises(BootstrapUnresolved):self.advance(h)
        self.assertTrue(h.unresolved);self.assertEqual(self.provider.calls,[])
        self.assertIsNone(self.db.receipt) # fake transaction confirms rollback
    def test_arbitrary_admission_object_cannot_enable_running_sql(self):
        h=self.start()
        class Forged:
            def check(self,*args):return True
        with self.assertRaises(BootstrapConflict):
            self.coordinator.journal.claim(5,9,'forged','a'*64,'b'*64,_admission=Forged())
        self.assertIsNone(self.db.receipt)
