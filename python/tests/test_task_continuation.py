"""Actual zero-timeout runtime fixture; no real provider or database."""
import unittest
from datetime import datetime,timezone
from artex.agent.task_continuation import TaskContinuationOwner
import test_task_runtime_application as base

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=base.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        # Cleanup is explicitly required for suspension receipts.
        from artex.agent.task_runtime import TaskRuntimeAssembly
        old=self.f.runtime
        self.f.runtime=TaskRuntimeAssembly(old.executor,providers=old.providers,transcript=old.transcript,prompts=old._prompts,policy_revision=old._revision,current=old._current,lifecycle_guard=old._guard,authorize=old._authorize,asset_authorize=old._assets,validate_input=old._input,graph_catalog=old._catalog,cleanup_authorize=lambda p:True)
        self.f.test_jwt_create_then_explicit_tick_uses_actual_goals_transport()
        self.app=self.f.bundle;self.handle=self.app.runner.handles()[0]
        self.allowed=True;self.owner=TaskContinuationOwner(self.app,authorize=lambda p:self.allowed)
        self.db=self.f.f.db;self.stamp=datetime(2026,1,1,tzinfo=timezone.utc)
        oldfetch=self.db.fetch_all
        def fetch(q):
            if q.sql.startswith('SELECT updated_at FROM tasks'):
                row=self.db.row
                return [(self.stamp,)] if row[7]=='running' and row[8] is q.params[2] else []
            if q.sql.startswith('SELECT id FROM exploration_nodes WHERE exploration_id=') and "state='running'" in q.sql:
                return [(r[0],) for r in self.db.rows if r[1]=='intent' and r[4]=='running'][:1]
            if q.sql.startswith("UPDATE tasks SET paused=false,queued=false,queued_at=NULL,queue_mode=''"):
                row=list(self.db.row)
                if row[7]!='running' or row[8] is not True or q.params[2]!=self.stamp:return []
                row[8]=False;self.db.row=tuple(row);return [(5,)]
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):
                r=self.handle._receipt
                return [(5,)] if q.params==(r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision) else []
            return oldfetch(q)
        self.db.fetch_all=fetch
    def test_constructor_inert_and_unissued_receipt_refused(self):
        before=len(self.db.calls);calls=list(self.f.p.calls)
        self.assertTrue(self.owner.validate_bindings())
        self.assertEqual(len(self.db.calls),before);self.assertEqual(self.f.p.calls,calls)
        with self.assertRaises(Exception):self.owner.prepare_resume(object(),{},object())
    def test_reservation_requires_current_zero_original_generation(self):
        self.allowed=False
        with self.assertRaises(Exception):self.owner.reserve_pause(self.handle)
        self.assertFalse(self.owner.retained(self.handle));self.assertFalse(self.handle.token.cancelled)
    def test_actual_first_continuation_advance_reuses_receipt_without_goals(self):
        self.app.host.install_continuation(self.owner);old=self.handle;receipt=old._receipt
        self.app.host.pause('5',timeout=0);before=list(self.f.p.calls)
        self.app.host.resume_suspended('5');new=self.app.runner.handles()[0]
        result=self.app.host.advance_owned(lambda:self.app.runner.advance('5',new.token,1.))
        self.assertEqual(result.value.action,'bootstrap_prepared')
        self.assertIs(new._receipt,receipt);self.assertIsNot(new,old);self.assertIsNot(new.token,old.token)
        self.assertEqual(self.f.p.calls,before);self.assertEqual(new.phase,'engine')
        self.assertIsNot(new._child,old._child);self.assertFalse(self.owner.retained(old))
        self.assertFalse(self.app.manager.owns_generation('5',old.token,old))
    def test_owned_worker_pause_cleanup_then_fresh_engine_and_worker(self):
        from threading import Event,Thread,Timer
        from artex.providers import Response
        import json
        self.app.host.install_continuation(self.owner);self.f.f.f.seed()
        entered=Event();release=Event();errors=[];calls=[]
        def transport(config):
            def invoke(*a):
                calls.append(1)
                if len(calls)==1:entered.set();release.wait(3)
                return Response(200,json.dumps({'choices':[{'message':{'role':'assistant','content':'inert worker'},'finish_reason':'stop'}]}))
            return invoke
        self.f.p.factory._transport_factory=transport;self.f.p.factory.invalidate()
        old=self.handle
        def work():
            try:self.app.host.advance_owned(lambda:self.app.runner.advance('5',old.token,1.))
            except BaseException as e:errors.append(e)
        thread=Thread(target=work);thread.start()
        try:
            self.assertTrue(entered.wait(2),errors);Timer(.05,release.set).start()
            self.app.host.pause('5',timeout=2);thread.join(2);self.assertFalse(errors)
            intent=next(r for r in self.db.rows if r[0]==3);self.assertEqual(intent[4],'open')
            self.assertFalse(old._child.cleanup._records)
            self.app.host.resume_suspended('5');new=self.app.runner.handles()[0]
            self.app.host.advance_owned(lambda:self.app.runner.advance('5',new.token,2.))
            self.assertEqual(calls,[1])
            result=self.app.host.advance_owned(lambda:self.app.runner.advance('5',new.token,3.))
            self.assertEqual(result.value.action,'worker');self.assertEqual(calls,[1,1])
            self.assertTrue(old.token.cancelled);self.assertFalse(new.token.cancelled)
        finally:release.set();thread.join(3)
    def test_resume_commit_response_failure_keeps_both_attempts(self):
        self.app.host.install_continuation(self.owner);self.app.host.pause('5',timeout=0)
        oldfetch=self.db.fetch_all
        def fail(q):
            result=oldfetch(q)
            if q.sql.startswith("UPDATE tasks SET paused=false,queued=false,queued_at=NULL,queue_mode=''"):raise RuntimeError('inert response loss')
            return result
        self.db.fetch_all=fail
        with self.assertRaises(Exception):self.app.host.resume_suspended('5')
        slot=self.app.manager._suspensions['5']
        self.assertEqual(slot['phase'],'quarantined');self.assertIsNotNone(slot['new_handle'])
        self.assertIs(slot['old_handle'],self.handle);self.assertTrue(self.owner.retained(self.handle))
        self.assertFalse(self.app.host.drain(0).all_settled)
        with self.assertRaises(Exception):self.app.host.resume_suspended('5')
    def test_continuation_pin_replacement_cannot_rebootstrap(self):
        self.app.host.install_continuation(self.owner);self.app.host.pause('5',timeout=0)
        self.app.host.resume_suspended('5');new=self.app.runner.handles()[0];before=list(self.f.p.calls)
        new._continuation=None
        with self.assertRaises(Exception):self.app.host.advance_owned(lambda:self.app.runner.advance('5',new.token,1.))
        self.assertEqual(self.f.p.calls,before)
