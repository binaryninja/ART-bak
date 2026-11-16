"""Actual owned worker pause/resume; inert provider and transaction fixture."""
import unittest
from threading import Event,Thread,Timer
from contextlib import contextmanager
from artex.agent.intent_control import IntentControl,IntentControlUnavailable,IntentControlUncertain
import test_engine_runner_bootstrap as base
from artex.agent.engine import TaskEngineHandle
from types import SimpleNamespace

class Tests(unittest.TestCase):
    def setUp(self):
        fixture=base.EngineBootstrapTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        b=fixture.f;old=b.child_factory
        def child(row,token,scope,current,guard):
            original=old(row,token,scope,current,guard)
            b.f.planner.current=lambda s:current(scope,token)
            b.f.worker.current=lambda s,p:current(scope,token)
            return TaskEngineHandle(scope,token,store=original.store,planner=original.planner,planner_scope=original.planner_scope,
                worker=original.worker,worker_model=original.worker_model,worker_system=original.worker_system,owner=original.owner,
                current=current,lifecycle_guard=guard,single_process=True,cleanup_authorize=lambda p:True,cleanup_current=lambda s:b.f.valid)
        b.child_factory=child;b.host.start('5');outer=fixture.runner.handles()[0];b.advance(outer)
        self.f=SimpleNamespace(f=b,h=outer)
        self.f.f.f.seed();self.outer=self.f.h;self.engine=self.outer._child
        self.host=self.f.f.host;self.runner=self.host.runner;self.db=self.f.f.db
        self.allowed=True;self.fail_resume=False
        self.adapter=IntentControl(self.host,authorize=lambda p:self.allowed)
        self.runner.install_intent_control(self.adapter)
        old=self.db.fetch_all;execute=self.db.execute
        receipt=self.outer._receipt
        def rows(q):
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):return [(5,)] if tuple(q.params)==tuple((receipt.task_id,receipt.exploration_id,receipt.run_id,receipt.request_id,receipt.config_digest,receipt.owner_token,receipt.revision)) else []
            if q.sql.startswith('SELECT state,content_version'):
                found=[r for r in self.db.rows if r[0]==q.params[0] and r[1]=='intent']
                return [(r[4],self.db.versions.get(r[0],0),r[6]) if 'owner' in q.sql else (r[4],self.db.versions.get(r[0],0)) for r in found]
            return old(q)
        def run(q):
            if q.sql.startswith("UPDATE exploration_nodes SET state='open'"):
                if self.fail_resume:raise RuntimeError('inert failure')
                for i,r in enumerate(self.db.rows):
                    if r[0]==q.params[0] and r[4]=='paused' and self.db.versions[r[0]]==q.params[2]:
                        row=list(r);row[4]='open';self.db.rows[i]=tuple(row);self.db.versions[r[0]]+=1;return 1
                return 0
            return execute(q)
        self.db.fetch_all=rows;self.db.execute=run
        self.entered=Event();self.release=Event();self.errors=[]
        def block():self.entered.set();self.release.wait(3)
        self.f.f.f.wp.hook=block
        self.thread=None
        self.addCleanup(self.cleanup_thread)
    def cleanup_thread(self):
        self.release.set()
        if self.thread:self.thread.join(3)
    def start(self):
        def work():
            try:self.f.f.advance(self.outer,1)
            except BaseException as e:self.errors.append(e)
        self.thread=Thread(target=work);self.thread.start();self.assertTrue(self.entered.wait(2))
    def test_pending_pause_then_exact_inspection(self):
        self.start();result=self.host.control_intent('5',3,'pause',timeout=0)
        self.assertEqual(result.phase,'pending');self.assertFalse(self.outer.token.cancelled)
        self.assertTrue(self.adapter.retained(self.outer))
        self.release.set();self.thread.join(2);self.assertFalse(self.errors)
        result=self.host.inspect_intent_control('5',3,timeout=0)
        self.assertEqual((result.phase,result.state,result.version),('confirmed','paused',2))
        self.assertFalse(self.adapter.retained(self.outer));self.assertFalse(self.outer.unresolved)
    def test_pause_resume_no_handler_model_and_next_pump_claim(self):
        self.start();Timer(.05,self.release.set).start()
        result=self.host.control_intent('5',3,'pause',timeout=2)
        self.thread.join(2);self.assertFalse(self.errors);self.assertEqual(result.state,'paused')
        count=len(self.f.f.f.wp.calls)
        result=self.host.control_intent('5',3,'resume',timeout=0)
        self.assertEqual((result.state,result.version),('open',3));self.assertEqual(len(self.f.f.f.wp.calls),count)
        self.assertFalse(self.outer.token.cancelled)
    def test_direct_foreign_denied_without_cancel(self):
        self.start()
        with self.assertRaises(Exception):self.adapter.execute(self.outer,3,'pause',0)
        with self.assertRaises(Exception):self.host.control_intent('5',4,'pause',timeout=0)
        self.assertFalse(self.outer.token.cancelled);self.assertFalse(self.adapter.retained(self.outer))
    def test_grant_denial_clears_clean_reservation(self):
        self.start();self.allowed=False
        with self.assertRaises(IntentControlUnavailable):self.host.control_intent('5',3,'pause',timeout=0)
        self.assertFalse(self.adapter.retained(self.outer));self.assertFalse(self.outer.unresolved)
    def test_resume_failure_retains_unknown_without_model(self):
        self.start();self.host.control_intent('5',3,'pause',timeout=0);self.release.set();self.thread.join(2)
        self.host.inspect_intent_control('5',3,timeout=0);self.fail_resume=True
        with self.assertRaises(IntentControlUncertain):self.host.control_intent('5',3,'resume',timeout=0)
        self.assertTrue(self.adapter.retained(self.outer));self.assertTrue(self.outer.unresolved)
        self.assertEqual(len(self.f.f.f.wp.calls),1)
