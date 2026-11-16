"""Independent candidate negatives; never substitute permissive validators."""
import unittest
from dataclasses import replace
from artex.agent.timed_admission import PreparedTimedAdmission,TimedAdmissionRequest
from artex.manager import RuntimeUnavailable
import test_timed_admission_candidate as fixture

class AdmissionCandidateReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_forged_exact_capability_cannot_rebind_runtime_digest(self):
        runtime=self.f.f.f.runtime;before=runtime._configuration_bytes
        cap=object.__new__(PreparedTimedAdmission)
        cap.runtime=runtime;cap.digest='f'*64;cap._pins_only=lambda:True
        with self.assertRaises(RuntimeUnavailable):runtime.bind_timed_admission(cap)
        self.assertIsNone(runtime._timed_admission)
        self.assertEqual(runtime._configuration_bytes,before)
    def test_foreign_final_resolver_refused_before_catalog_or_inventory(self):
        f=self.f;before=list(f.f.f.f.db.calls)
        from artex.agent.final_task_provider import FinalTaskProviderAdapter
        import test_task_providers as providers
        p=providers.Tests();p.setUp();self.addCleanup(p.doCleanups)
        policy=replace(f.policy,final_plan=replace(f.plan,task_provider=FinalTaskProviderAdapter(p.resolver)))
        with self.assertRaises((ValueError,RuntimeUnavailable)):
            f.f.f.build(timed_lifecycle_policy=policy,track_manual_actions=True,timed_admission_request=TimedAdmissionRequest('db'))
        self.assertEqual(f.f.f.f.db.calls,before)
    def test_foreign_clock_refused_without_runtime_mutation(self):
        f=self.f;runtime=f.f.f.runtime;before=runtime._configuration_bytes
        policy=replace(f.policy,final_plan=replace(f.plan,clock=lambda:100.))
        with self.assertRaises(RuntimeUnavailable):
            f.f.f.build(timed_lifecycle_policy=policy,track_manual_actions=True,timed_admission_request=TimedAdmissionRequest('db'))
        self.assertIsNone(runtime._timed_admission);self.assertEqual(runtime._configuration_bytes,before)

    def test_copied_unissued_object_cannot_gain_issuance(self):
        from copy import copy
        from artex.agent.timed_admission import validate_issued_admission
        runtime=self.f.f.f.runtime;before=runtime._configuration_bytes
        original=object.__new__(PreparedTimedAdmission)
        original.runtime=runtime;original.digest='f'*64;original._pins_only=lambda:True
        for cap in (original,copy(original)):
            with self.assertRaises(RuntimeUnavailable):validate_issued_admission(cap,runtime)
            with self.assertRaises(RuntimeUnavailable):runtime.bind_timed_admission(cap)
        self.assertIsNone(runtime._timed_admission)
        self.assertEqual(runtime._configuration_bytes,before)

    def test_actual_schema_refusal_never_issues_capability_or_changes_digest(self):
        import artex.agent.timed_admission as module
        f=self.f;runtime=f.f.f.runtime;before=runtime._configuration_bytes
        with module._ISSUANCE_LOCK:issued=tuple(module._ISSUED)
        # Existing fixture is not a compatible catalog snapshot. Use real
        # validators and do not replace either with a success observation.
        with self.assertRaises(Exception):module.prepare_timed_admission(runtime,f.policy,TimedAdmissionRequest('not-a-valid-catalog'))
        with module._ISSUANCE_LOCK:self.assertEqual(tuple(module._ISSUED),issued)
        self.assertIsNone(runtime._timed_admission)
        self.assertEqual(runtime._configuration_bytes,before)
        self.assertEqual(f.f.f.p.calls,[])
