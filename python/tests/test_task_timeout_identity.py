"""Configuration binding only. Runtime positive admission still rejects."""
import unittest,json,hashlib
from dataclasses import asdict
from artex.manager import RuntimeUnavailable
from artex.db.tasks import TaskCreateOptions
import test_task_runtime_timing as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.task=self.f.f.p.task;self.task.id=5;self.task.timeout_seconds=0
        self.a=self.f.make(self.f.policy)
        self.snapshot=self.f.f.p.snapshot
    def provisional(self,timeout):return {'provisional':True,'options':TaskCreateOptions(llm_profile_ids=[2],timeout_seconds=timeout)}
    def test_positive_provisional_persisted_same_identity_and_different_durations(self):
        a=self.a.bootstrap_configuration(self.provisional(10));self.task.timeout_seconds=10
        b=self.a.bootstrap_configuration(self.snapshot);self.assertEqual(a,b)
        self.task.timeout_seconds=11;c=self.a.bootstrap_configuration(self.snapshot)
        self.assertNotEqual(a.policy_revision,c.policy_revision)
        self.assertFalse(self.a._bootstrap_current(5,9,a.policy_revision))
        self.assertEqual(self.f.f.p.calls,[]);self.assertEqual(self.f.calls,[])
    def test_zero_digest_preserves_prior_payload(self):
        models,digest=self.a._select(self.snapshot)
        payload={'models':{k:asdict(v) for k,v in models.items()},'prompts':self.a._prompts,
            'catalog':{k:[asdict(r) for r in v] for k,v in self.a._catalog.items()},'limits':asdict(self.a._limits),
            'policy':self.a._revision,'activity_projection':False,'inflight_cleanup':True,
            'task_timing':{'enabled':True,'revision':self.f.policy.revision}}
        self.assertEqual(digest,hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest())
        self.assertEqual(self.a.bootstrap_configuration(self.provisional(0)),self.a.bootstrap_configuration(self.snapshot))
    def test_invalid_provisional_and_persisted_values(self):
        for value in (-1,True,None,'10',1.5,2**31):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeUnavailable):self.a.bootstrap_configuration(self.provisional(value))
                self.task.timeout_seconds=value
                with self.assertRaises(RuntimeUnavailable):self.a.bootstrap_configuration(self.snapshot)
        del self.task.timeout_seconds
        with self.assertRaises(RuntimeUnavailable):self.a.bootstrap_configuration(self.snapshot)
    def test_conflicting_snapshot_or_options_rejected(self):
        with self.assertRaises(RuntimeUnavailable):self.a.bootstrap_configuration(dict(self.provisional(10),timeout_seconds=11))
        with self.assertRaises(RuntimeUnavailable):self.a.bootstrap_configuration(dict(self.snapshot,timeout_seconds=10))
        with self.assertRaises(RuntimeUnavailable):self.a.bootstrap_configuration(dict(self.snapshot,options={}))
    def test_role_callback_timeout_drift_rejected(self):
        original=self.a.providers.select;calls=[]
        def select(snapshot,role):
            result=original(snapshot,role);calls.append(role)
            if role=='worker':self.task.timeout_seconds=10
            return result
        self.a.providers.select=select
        with self.assertRaises(RuntimeUnavailable):self.a.bootstrap_configuration(self.snapshot)
    def test_positive_requires_exact_optin(self):
        plain=self.f.make(None)
        with self.assertRaises(RuntimeUnavailable):plain.bootstrap_configuration(self.provisional(10))
        object.__setattr__(self.f.policy,'clock',None)
        with self.assertRaises(ValueError):self.f.make(self.f.policy)
    def test_persisted_timeout_read_is_granted_before_fetch(self):
        self.a.providers.authorize_task=lambda *a:False;called=[]
        self.a.providers.tasks.get=lambda id:called.append(id) or self.task
        with self.assertRaises(Exception):self.a.bootstrap_configuration(self.snapshot)
        self.assertEqual(called,[])
