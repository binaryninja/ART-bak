"""Explicit cancelled-generation finalization; inert actual engine/pump path."""
import unittest
from threading import Thread,Event
from artex.agent.planner import PlannerCycle
from artex.agent.chat import ChatBinding,ChatPersistenceError
from artex.agent.runtime import Grant
from artex.agent.pump import EnginePump
from artex.manager import TaskConflict
from artex.transcripts import TranscriptStore
import test_engine_runner_review as fixtures
from test_chat_runtime import text

class PlannerPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.h=fixtures.EngineRunnerReview();self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.f=self.h.f;self.persist_valid=True;self.persist_calls=[]
    def configure(self,enabled=True):
        original=self.f.planner
        def persist(scope):
            self.persist_calls.append(scope)
            return self.persist_valid and self.f.valid and scope==self.f.ps
        self.f.planner=PlannerCycle(store=self.f.graph,transcript=original.transcript,
            current=lambda s:self.f.valid and s==self.f.ps and not self.f.token.cancelled,
            lifecycle_guard=self.f.guard,provider_resolver=lambda s,m:ChatBinding(m,self.f.pp),
            authorize=lambda *a:Grant(True,True,True),asset_authorize=lambda *a:Grant(True,True,True),
            persist_current=persist if enabled else None)
        self.f.pp.responses=[text('partial cancelled reply')]
    def run_paused(self,*,enabled=True,stale=False):
        self.configure(enabled)
        self.h.host.start('5');handle=self.h.runner.handles()[0]
        pump=EnginePump(self.h.host,clock=lambda:0);self.addCleanup(lambda:pump.stop(2))
        entered=Event();release=Event();original=self.f.pp.complete
        def blocked(*args):
            entered.set();release.wait(2);return original(*args)
        self.f.pp.complete=blocked
        outcomes=[]
        def tick():
            try:outcomes.append(pump.tick())
            except BaseException as exc:outcomes.append(exc)
        thread=Thread(target=tick);thread.start()
        self.addCleanup(release.set);self.addCleanup(lambda:thread.join(2))
        self.assertTrue(entered.wait(1))
        with self.assertRaises(TaskConflict):self.h.host.pause('5',timeout=0)
        self.assertTrue(handle.token.cancelled)
        if stale:self.persist_valid=False
        release.set();thread.join(2);self.assertFalse(thread.is_alive())
        return handle,pump,outcomes
    def test_actual_pump_pause_persists_without_quarantine_or_node_read(self):
        # Fail if finalization accidentally performs graph observation after pause.
        configure=self.configure
        def configured(enabled=True):
            configure(enabled);core=self.f.planner;read=core._nodes
            def checked():
                if self.f.token.cancelled:self.fail('execution graph read after cancellation')
                return read()
            core._nodes=checked
        self.configure=configured
        handle,pump,outcomes=self.run_paused()
        self.assertFalse(handle.unresolved);self.assertEqual(pump.failure,'')
        self.assertFalse(isinstance(outcomes[0],BaseException));self.assertEqual(len(self.persist_calls),1)
        records=self.f.planner.transcript.store.read('exp9-planner')
        self.assertTrue(any(r.get('_artex_chat',{}).get('phase')=='finalized' for r in records))
        self.assertTrue(self.h.host.pause('5')['paused'])
        self.assertEqual(self.h.host.poll().settled,());self.assertEqual(self.h.runner.handles(),())
    def test_default_execution_authority_remains_fail_closed(self):
        handle,pump,outcomes=self.run_paused(enabled=False)
        self.assertTrue(handle.unresolved);self.assertTrue(pump.failure)
        self.assertIsInstance(outcomes[0],BaseException);self.assertEqual(self.persist_calls,[])
    def test_stale_persistence_generation_still_quarantines(self):
        handle,pump,outcomes=self.run_paused(stale=True)
        self.assertTrue(handle.unresolved);self.assertTrue(pump.failure)
        self.assertIsInstance(outcomes[0],BaseException)
    def test_cancelled_result_explicitly_has_no_graph_observation(self):
        self.configure();core=self.f.planner
        self.f.pp.hook=lambda:core.cancel(5)
        outcome=core.run(core.prepare(self.f.ps,'cancelled-cycle'))
        self.assertTrue(outcome.outcome.persisted);self.assertEqual(outcome.outcome.result.reason,'cancelled')
        self.assertFalse(outcome.observed)
        self.assertIsNone(outcome.active_intents);self.assertIsNone(outcome.goals_met);self.assertIsNone(outcome.stalled)
    def test_persistence_grant_never_authorizes_admission(self):
        self.configure();self.f.token.cancel('paused')
        with self.assertRaises(Exception):self.f.planner.prepare(self.f.ps,'forbidden')
        self.assertEqual(self.persist_calls,[]);self.assertEqual(self.f.pp.calls,[])

    def test_persist_authority_does_not_enable_cancelled_model_tool(self):
        from test_planner_cycle import intent
        self.configure();core=self.f.planner
        self.f.pp.responses=[intent('must not persist')]
        def cancel():
            self.f.token.cancel('paused');core.cancel(5)
        self.f.pp.hook=cancel
        outcome=core.run(core.prepare(self.f.ps,'no-tool'))
        self.assertFalse(outcome.observed);self.assertTrue(outcome.outcome.persisted)
        self.assertFalse(any(row[1]=='intent' for row in self.f.db.rows))
        self.assertEqual(len(self.persist_calls),1)

    def test_cancel_racing_post_commit_observation_does_not_quarantine_receipt(self):
        self.configure();core=self.f.planner;committed=False
        original=core.transcript.commit
        def commit(*args):
            nonlocal committed
            result=original(*args);committed=True;return result
        core.transcript.commit=commit
        def current(scope):
            if committed:
                self.f.token.cancel('paused');core.cancel(5);return False
            return True
        core.current=current
        result=core.run(core.prepare(self.f.ps,'late-cancel'))
        self.assertTrue(result.outcome.persisted);self.assertFalse(result.observed)
    def test_persistence_callback_cannot_rebind_executor(self):
        self.configure();core=self.f.planner
        def persistence(scope):core.store.executor=object();return True
        core.persist_current=persistence
        with self.assertRaises(ChatPersistenceError):core.run(core.prepare(self.f.ps,'rebind'))
