"""Independent generation/admission/settlement review using inert fixture DB."""
from dataclasses import replace
import unittest
import test_worker_cycle as fixtures
from artex.agent.worker import WorkerUnresolved

class WorkerReviewTests(unittest.TestCase):
    setUp=fixtures.WorkerCycleTests.setUp
    registry=fixtures.WorkerCycleTests.registry
    new_worker=fixtures.WorkerCycleTests.new_worker
    def test_invalid_streaming_model_rejected_before_durable_claim(self):
        with self.assertRaises(Exception):self.worker.run(replace(self.scope,model=replace(self.scope.model,streaming=True)),'client')
        self.assertEqual(self.db.rows[0][4],'open')
        self.assertEqual(self.db.rows[0][10],3)
        self.assertEqual(self.provider.calls,[])
    def test_empty_model_identity_rejected_before_durable_claim(self):
        with self.assertRaises(Exception):self.worker.run(replace(self.scope,model=replace(self.scope.model,identity='')),'client')
        self.assertEqual(self.db.rows[0][4],'open');self.assertEqual(self.db.rows[0][10],3)
        self.assertEqual(self.provider.calls,[])
    def test_cancellation_accepted_at_settlement_not_reported_done(self):
        checks=0;accepted=[]
        def current(scope,phase):
            nonlocal checks
            if phase=='settle':
                checks+=1
                # First settle check protects transcript commit; second is SQL settlement.
                if checks==2:accepted.append(self.worker.cancel(7,'worker_pause'))
            return True
        self.worker.current=current
        result=self.worker.run(self.scope,'client')
        self.assertEqual(accepted,[True])
        self.assertEqual(result.state,'paused')
        self.assertEqual(self.db.rows[0][4],'paused')
    def test_cancel_during_preflight_never_claims(self):
        original=self.worker.provider
        def provider(scope,model):
            self.worker.cancel(7,'task_pause');return original(scope,model)
        self.worker.provider=provider
        with self.assertRaises(Exception):self.worker.run(self.scope,'client')
        self.assertEqual(self.db.rows[0][4],'open');self.assertEqual(self.db.rows[0][10],3)
        self.assertEqual(self.provider.calls,[])

    def test_late_cancel_rejected_after_settlement_decision(self):
        accepted=[];original=self.db.execute
        def execute(query):
            if query.sql.startswith('UPDATE exploration_nodes SET state=$1'):
                accepted.append(self.worker.cancel(7,'worker_pause'))
            return original(query)
        self.db.execute=execute
        result=self.worker.run(self.scope,'client')
        self.assertEqual(accepted,[False]);self.assertEqual(result.state,'done')
        self.assertEqual(self.db.rows[0][4],'done')

    def test_frozen_settlement_failure_is_unresolved_not_false_success(self):
        accepted=[];original=self.db.execute;self.db.fail_settle=True
        def execute(query):
            if query.sql.startswith('UPDATE exploration_nodes SET state=$1'):
                accepted.append(self.worker.cancel(7,'worker_pause'))
            return original(query)
        self.db.execute=execute
        with self.assertRaises(WorkerUnresolved):self.worker.run(self.scope,'client')
        self.assertEqual(accepted,[False]);self.assertEqual(self.db.rows[0][4],'running')
