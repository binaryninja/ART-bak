"""Worker glue tests with a mocked cleanup owner, not capability-security proof.

Real owner, manager/freeze and storage validation have separate integration tests.
These fixtures verify that the worker passes only the already-claimed turn and
never falls back to ordinary settlement when cleanup reports uncertainty.
"""
from dataclasses import replace
from types import ModuleType
from unittest.mock import patch
import unittest
import test_worker_cycle as fixtures
from artex.agent.worker import WorkerUnresolved
from artex.db.workers import WorkerConflict


class Cleanup:
    def __init__(self, fixture):
        self.f=fixture;self.cap=object();self.calls=[];self.fail_commit=False;self.fail_settle=False
        self.worker_store=fixture.store;self.transcript=fixture.transcript
    def reserve(self,role,scope,session,turn,identity,*,worker_store):
        record=worker_store.get(scope.intent_id)
        assert record.node.state=='running' and record.version==scope.expected_version+1
        assert turn.created
        self.calls.append(('reserve',role,scope,session,turn,identity,worker_store))
        self.session=session;self.turn=turn
        return self.cap
    def commit(self,cap,turn,result):
        assert cap is self.cap
        self.calls.append(('commit',result))
        if self.fail_commit:raise RuntimeError('inert receipt failure')
        self.result=result if self.f.valid else replace(result,reason='cancelled')
        return self.f.transcript.commit(self.session,turn,self.result)
    def canonical_outcome(self,cap,outcome):
        assert cap is self.cap
        return replace(outcome,result=self.result)
    def worker_settle(self,cap,store,state,*,cancellation_reason):
        assert cap is self.cap
        self.calls.append(('settle',state,cancellation_reason))
        if self.fail_settle:return False
        return store.transition(7,'running',state,4)
    def quarantine(self,cap):
        assert cap is self.cap
        self.calls.append(('quarantine',))


class WorkerCleanupHookTests(unittest.TestCase):
    setUp=fixtures.WorkerCycleTests.setUp
    registry=fixtures.WorkerCycleTests.registry
    new_worker=fixtures.WorkerCycleTests.new_worker
    def cleanup_worker(self):
        module=ModuleType('artex.agent.inflight_cleanup');module.InflightCleanupOwner=Cleanup
        patcher=patch.dict('sys.modules',{'artex.agent.inflight_cleanup':module})
        patcher.start();self.addCleanup(patcher.stop)
        self.cleanup=Cleanup(self)
        self.worker=self.new_worker(cleanup=self.cleanup)
    def test_exact_created_claim_and_cap_are_forwarded_without_finish(self):
        self.cleanup_worker()
        result=self.worker.run(self.scope,'cleanup')
        self.assertIs(result.cleanup,self.cleanup.cap)
        self.assertEqual([c[0] for c in self.cleanup.calls],['reserve','commit','settle'])
        self.assertEqual(self.cleanup.calls[-1],('settle','done',None))
        self.assertEqual(len(self.provider.calls),1)
    def test_revocation_allows_cleanup_not_more_execution(self):
        self.cleanup_worker()
        self.provider.hook=lambda:setattr(self,'valid',False)
        result=self.worker.run(self.scope,'revoked')
        self.assertEqual(result.state,'blocked')
        self.assertEqual(len(self.provider.calls),1);self.assertEqual(self.calls,[])
        self.assertEqual(self.db.rows[0][4],'blocked')
    def test_explicit_cancel_cause_forwarded(self):
        self.cleanup_worker()
        self.provider.hook=lambda:self.worker.cancel(7,'task_pause')
        result=self.worker.run(self.scope,'paused')
        self.assertEqual(result.state,'open')
        self.assertEqual(self.cleanup.calls[-1],('settle','open','task_pause'))
    def test_failed_commit_quarantines_without_settlement(self):
        self.cleanup_worker();self.cleanup.fail_commit=True
        with self.assertRaises(WorkerUnresolved):self.worker.run(self.scope,'uncertain')
        self.assertEqual([c[0] for c in self.cleanup.calls],['reserve','commit','quarantine'])
        self.assertEqual(self.db.rows[0][4],'running')
    def test_failed_settlement_quarantines_without_fallback(self):
        self.cleanup_worker();self.cleanup.fail_settle=True
        with self.assertRaises(WorkerUnresolved):self.worker.run(self.scope,'uncertain')
        self.assertEqual([c[0] for c in self.cleanup.calls],['reserve','commit','settle','quarantine'])
        self.assertEqual(self.db.rows[0][4],'running')
        with self.assertRaises(WorkerConflict):self.worker.run(self.scope,'retry')
        self.assertEqual(len(self.provider.calls),1)
    def test_changed_cleanup_binding_fails_closed(self):
        self.cleanup_worker()
        self.provider.hook=lambda:setattr(self.worker,'cleanup',None)
        with self.assertRaises(WorkerUnresolved):self.worker.run(self.scope,'changed')
        self.assertEqual([c[0] for c in self.cleanup.calls],['reserve','quarantine'])
        self.assertEqual(self.db.rows[0][4],'running')
    def test_no_cleanup_is_default(self):
        result=self.worker.run(self.scope,'ordinary')
        self.assertIsNone(result.cleanup)
    def test_cleanup_binding_is_once_before_admission(self):
        self.cleanup_worker()
        worker=self.new_worker()
        worker.bind_cleanup(self.cleanup)
        with self.assertRaises(WorkerConflict):worker.bind_cleanup(self.cleanup)
        ordinary=self.new_worker()
        ordinary.run(self.scope,'pre-bind')
        with self.assertRaises(WorkerConflict):ordinary.bind_cleanup(self.cleanup)
    def test_foreign_resource_binding_rejected(self):
        self.cleanup_worker()
        self.cleanup.worker_store=object()
        with self.assertRaises(ValueError):self.new_worker(cleanup=self.cleanup)
    def test_arbitrary_cleanup_object_rejected(self):
        self.cleanup_worker()
        with self.assertRaises(ValueError):self.new_worker(cleanup=object())


class RealWorkerCleanupTests(unittest.TestCase):
    """Actual host/outer/engine worker path; inert SQL/model and real JSONL."""
    def setUp(self):
        import test_inflight_cleanup as fixture
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.f.f.seed()
        self.worker=self.f.h._child.worker
    def test_real_owner_settles_and_engine_finishes(self):
        value=self.f.f.advance(self.f.h,1)
        self.assertEqual(value.action,'worker')
        self.assertEqual(self.worker.store.get(3).node.state,'done')
        self.assertEqual(self.worker.store.get(3).version,2)
        self.assertFalse(self.f.f.manager._has_cleanup('5'))
        self.assertEqual(len(self.f.f.f.wp.calls),1)
    def test_pause_during_provider_uses_explicit_task_pause_state(self):
        self.f.f.f.wp.hook=lambda:self.f.f.host.pause('5',timeout=0)
        value=self.f.f.advance(self.f.h,1)
        self.assertEqual(value.action,'worker')
        self.assertEqual(self.worker.store.get(3).node.state,'open')
        self.assertEqual(len(self.f.f.f.wp.calls),1)
        self.assertFalse(self.f.f.manager._has_cleanup('5'))
        self.assertFalse(self.f.h.unresolved)
    def test_real_store_failure_retains_capability_and_blocks_replay(self):
        from artex.agent.bootstrap import BootstrapUnresolved
        self.f.f.db.fail_settle=True
        with self.assertRaises(BootstrapUnresolved):self.f.f.advance(self.f.h,1)
        self.assertTrue(self.f.f.manager._has_cleanup('5'))
        self.assertEqual(self.worker.store.get(3).node.state,'running')
        with self.assertRaises(Exception):self.f.f.advance(self.f.h,2)
        self.assertEqual(len(self.f.f.f.wp.calls),1)
    def test_idle_direct_worker_denied_before_claim(self):
        from artex.agent.worker import WorkerScope
        scope=WorkerScope(5,9,self.f.h.scope.run_id,self.f.h.scope.revision,3,0,'fixture',fixtures.MODEL,'inert')
        with self.assertRaises(Exception):self.worker.run(scope,'idle')
        self.assertEqual(self.worker.store.get(3).node.state,'open')
        self.assertEqual(self.f.f.f.wp.calls,[])
