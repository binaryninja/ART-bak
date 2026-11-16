"""Default and forged-capability negatives; no permissive positive schema mocks."""
import unittest
from copy import deepcopy
from artex.agent.bootstrap_handle import BootstrapHandle
from artex.agent.timed_admission import PreparedTimedAdmission
from artex.manager import RuntimeUnavailable
import test_bootstrap_setup as setup_fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=setup_fixture.BootstrapSetupTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_default_positive_rejected_before_config_or_insert(self):
        b=self.f.build();before=len(self.f.f.db.calls)
        with self.assertRaises(RuntimeUnavailable):b.runner.validate_create('inert','goal',{'timeout_seconds':10})
        self.assertEqual(self.f.selections,[]);self.assertEqual(len(self.f.f.db.calls),before)
        self.assertEqual(b.runner.handles(),())
    def test_explicit_foreign_cap_rejected_before_startup_reads(self):
        before=len(self.f.f.db.calls)
        with self.assertRaises(ValueError):self.f.build(timed_admission=object())
        self.assertEqual(len(self.f.f.db.calls),before)
    def test_runner_cannot_install_foreign_or_rebound_capability(self):
        b=self.f.build()
        with self.assertRaises(RuntimeUnavailable):b.runner.install_timed_admission(object())
        cap=object.__new__(PreparedTimedAdmission)
        cap.validate_bindings=lambda:True;cap.validate_task=lambda *a,**k:True;cap.validate_handle_configuration=lambda *a,**k:True
        with self.assertRaises(RuntimeUnavailable):b.runner.install_timed_admission(cap)
        self.assertIsNone(b.runner._timed_admission)
    def test_direct_positive_handle_requires_original_capability(self):
        b=self.f.build();b.facade.start('5');h=b.runner.handles()[0]
        task=deepcopy(h._task);task['timeout_seconds']=10
        args=dict(coordinator=h.coordinator,manager=h.manager,model=h.model,system=h.system,
                  policy_revision=h.policy_revision,request_id=h.request_id,engine_factory=self.f.f.child_factory)
        with self.assertRaises(ValueError):BootstrapHandle(task,h.token,**args)
        cap=object.__new__(PreparedTimedAdmission);cap.validate_bindings=lambda:True;cap.validate_handle_configuration=lambda *a,**k:True
        with self.assertRaises(ValueError):BootstrapHandle(task,h.token,timed_admission=cap,**args)
        self.assertIsNone(self.f.f.db.receipt)
    def test_unsupported_options_rejected_default_and_zero_no_cap(self):
        b=self.f.build()
        for options in ({'timeout_seconds':10,'source_task_ids':[1]}, {'timeout_seconds':10,'parent_ref':'x'}, {'timeout_seconds':10,'intercept_rules':[{}]}):
            with self.assertRaises(RuntimeUnavailable):b.runner.validate_create('x','y',options)
        self.assertIsNone(b.runner.timed_admission_for({'timeout_seconds':0},policy_revision='r1'))
        self.assertEqual(self.f.selections,[])
