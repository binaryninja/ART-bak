"""Actual registered timed worker; synthetic prepared-timeout fixture only."""
import unittest,json
from threading import Event,Thread
from artex.agent.intent_control import IntentControl
from artex.providers import Response
import test_timed_engine_branch as base

class Tests(unittest.TestCase):
    def setUp(self):
        f=base.Tests();f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.clock=f.stamp_original_clock();self.db=f.f.f.f.db
        f.f.f.f.f.seed();self.expired=False;old=self.db.fetch_all
        def query(q):
            if q.sql.startswith('SELECT timeout_seconds,first_run_at,deadline_at,deadline_at<=clock_timestamp()'):return [(10,self.clock.first_run_at,self.clock.deadline_at,self.expired)]
            if q.sql.startswith('SELECT t.id,t.first_run_at,t.deadline_at'):return [(5,self.clock.first_run_at,self.clock.deadline_at)]
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):
                r=f.actions.receipt;return [(5,)] if q.params==(r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision) else []
            if q.sql.startswith('SELECT state,content_version,owner'):
                return [(r[4],self.db.versions.get(r[0],0),r[6]) for r in self.db.rows if r[0]==q.params[0] and r[1]=='intent']
            return old(q)
        self.db.fetch_all=query;self.entered=Event();self.release=Event();self.errors=[];self.calls=[]
        def transport(config):
            def invoke(*args):
                self.calls.append(1);self.entered.set();self.release.wait(3)
                return Response(200,json.dumps({'choices':[{'message':{'role':'assistant','content':'inert'},'finish_reason':'stop'}]}))
            return invoke
        f.f.f.p.factory._transport_factory=transport;f.f.f.p.factory.invalidate()
        self.adapter=IntentControl(f.bundle.host,authorize=lambda p:True);f.bundle.runner.install_intent_control(self.adapter)
        def run():
            try:f.bundle.host.advance_owned(lambda:f.bundle.runner.advance('5',f.handle.token,1.))
            except BaseException as e:self.errors.append(e)
        self.thread=Thread(target=run);self.thread.start();self.addCleanup(self.finish)
        self.assertTrue(self.entered.wait(2),self.errors)
    def finish(self):self.release.set();self.thread.join(3)
    def test_pending_pause_expiry_clears_only_original_control(self):
        f=self.f;self.assertEqual(f.bundle.host.control_intent('5',3,'pause',timeout=0).phase,'pending')
        self.expired=True;self.assertEqual(f.actions.observer.tick().phase,'draining')
        self.finish();self.assertFalse(self.errors)
        result=f.bundle.host.inspect_intent_control('5',3,timeout=0)
        self.assertEqual((result.phase,result.state,result.version),('confirmed','paused',2))
        self.assertFalse(self.adapter.retained(f.handle));self.assertFalse(f.handle.unresolved)
        self.assertEqual(f.actions.observer.tick().phase,'ready');self.assertEqual(self.calls,[1])
        with self.assertRaises(Exception):f.bundle.host.control_intent('5',3,'resume',timeout=0)
