import unittest
from artex.manager import TaskManager,RuntimeUnavailable
from artex.agent.engine_runner import EngineRunner
from artex.agent.engine import EngineScope
from test_manager import Repository,record,Runner

class Repo(Repository):
    def __init__(self,*a):super().__init__(*a);self.writes=[]
    def apply_admission(self,*a):self.writes.append(a);return super().apply_admission(*a)

class PreflightTests(unittest.TestCase):
    def manager(self,queued=False):
        self.repo=Repo(record(5,exploration_id=9,queued=queued,queue_mode='bootstrap' if queued else ''))
        self.calls=[]
        runner=EngineRunner(readiness=lambda t:EngineScope(5,9,1,'r1'),factory=lambda *a:self.calls.append(a))
        m=TaskManager(self.repo,runner=runner);m.load_existing();return m
    def test_unsupported_seed_zero_admission(self):
        m=self.manager()
        with self.assertRaises(RuntimeUnavailable):m.start('5',seed_first_intent=True)
        self.assertEqual(self.repo.writes,[]);self.assertEqual(self.calls,[])
        self.assertEqual(self.repo.rows[5]['status'],'created');self.assertEqual(m._runs,{})
    def test_invalid_seed_zero_admission(self):
        for value in (1,None,'false'):
            m=self.manager()
            with self.assertRaises(ValueError):m.start('5',seed_first_intent=value)
            self.assertEqual(self.repo.writes,[]);self.assertEqual(m._runs,{})
    def test_queue_metadata_before_admission(self):
        m=self.manager(True);seen=[]
        m.runner.ready=lambda row:seen.append(row) or (_ for _ in ()).throw(RuntimeUnavailable('unsupported'))
        with self.assertRaises(RuntimeUnavailable):m.start_queued('5',m.lifecycle_snapshot('5'))
        self.assertEqual(seen[0]['admission_mode'],'bootstrap');self.assertIs(seen[0]['seed_first_intent'],False)
        self.assertEqual(self.repo.writes,[])
    def test_ready_cannot_mutate_admission_expected_status(self):
        repo=Repo();runner=Runner();m=TaskManager(repo,runner=runner);m.load_existing()
        def ready(row):row['status']='tampered';return True
        runner.ready=ready
        self.assertTrue(m.start('1'));self.assertEqual(repo.writes[0][1],'created')
