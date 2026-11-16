"""Independent immutable retirement evidence checks; inert owned fixture only."""
import unittest
from dataclasses import replace
import test_frontier_completion as fixture
from artex.agent.frontier_completion import FrontierUnavailable

class RetirementReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.run=self.f.host.manager._runs['5']
        self.f.component.run_once()
    def test_consumed_capability_identity_fields_remain_pinned(self):
        f=self.f
        object.__setattr__(f.component._store._cap,'task_id','6')
        with self.assertRaises(FrontierUnavailable):f.component.retirement_result(f.host.runner)
    def test_original_result_mutation_rejected(self):
        f=self.f
        object.__setattr__(f.component.result,'task_id',6)
        with self.assertRaises(FrontierUnavailable):f.component.retirement_result(f.host.runner)
    def test_copied_private_and_public_result_rejected(self):
        f=self.f;c=f.component
        c.result=c._store._result=replace(c.result)
        with self.assertRaises(FrontierUnavailable):c.retirement_result(f.host.runner)
    def test_registry_timestamp_change_rejected(self):
        f=self.f
        f.host.manager._tasks['5']['completed_at']='different'
        with self.assertRaises(FrontierUnavailable):f.component.retirement_result(f.host.runner)
    def test_consumed_evidence_cannot_be_reintroduced(self):
        f=self.f
        f.component._store._evidence=object()
        with self.assertRaises(FrontierUnavailable):f.component.retirement_result(f.host.runner)
    def test_execution_registry_reattachment_rejected(self):
        f=self.f
        f.host.manager._runs['5']=self.run
        with self.assertRaises(FrontierUnavailable):f.component.retirement_result(f.host.runner)
