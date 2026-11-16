"""Independent timeout read disclosure/drift checks; no task admission."""
import unittest
import test_task_timeout_identity as fixture

class TimeoutIdentityReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_fetch_revokes_original_read_grant_no_configuration(self):
        f=self.f;allowed=[True];original=f.a.providers.tasks.get
        f.a.providers.authorize_task=lambda *a:allowed[0]
        def get(task_id):
            result=original(task_id);allowed[0]=False;return result
        f.a.providers.tasks.get=get
        with self.assertRaises(Exception):f.a.bootstrap_configuration(f.snapshot)
        self.assertEqual(f.f.f.p.calls,[])
    def test_fetch_swaps_original_policy_no_configuration(self):
        f=self.f;original=f.a.providers.tasks.get
        def get(task_id):
            result=original(task_id);f.a.providers.authorize_task=lambda *a:True;return result
        f.a.providers.tasks.get=get
        with self.assertRaises(Exception):f.a.bootstrap_configuration(f.snapshot)
    def test_final_read_grant_timeout_drift_does_not_return_old_digest(self):
        f=self.f;reads=[];tail=[];original=f.a.providers.tasks.get
        def get(task_id):reads.append(task_id);return original(task_id)
        def grant(*args):
            if len(reads)==5:
                tail.append(True)
                if len(tail)==2:f.task.timeout_seconds=10
            return True
        f.a.providers.tasks.get=get;f.a.providers.authorize_task=grant
        with self.assertRaises(Exception):f.a.bootstrap_configuration(f.snapshot)
        self.assertGreaterEqual(len(tail),2)
