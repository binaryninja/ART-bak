"""Independent synthetic final-disclosure regression; no live logs or database."""
import unittest
from contextlib import contextmanager
from artex.db.task_coverage import TaskCoverageStore,CoverageScope,CoverageDenied
from artex.server.task_coverage import TaskCoverageService
from artex.server.core import Request
from test_tasks_store import Fake

class CoverageGuardExitReview(unittest.TestCase):
    def fixture(self,revoke_exit):
        allowed=[True];exits=[0]
        @contextmanager
        def guard(scope):
            yield
            exits[0]+=1
            if exits[0]==revoke_exit:allowed[0]=False
        db=Fake([[(5,9,True)],[(6,10,None,False)],[(1,)],[('ip',1,0)]])
        store=TaskCoverageStore(db,CoverageScope(5,9,'read-1'),authorize=lambda s,t,e:allowed[0],current=lambda s:True,lifecycle_guard=guard)
        return db,store,guard,allowed
    def test_direct_store_rechecks_source_grant_after_guard_exit(self):
        db,store,guard,allowed=self.fixture(1)
        with self.assertRaises(CoverageDenied):store.coverage()
    def test_http_rechecks_source_grant_after_final_guard_exit(self):
        db,store,guard,allowed=self.fixture(2)
        service=TaskCoverageService(db,resolve=lambda req,t:store,authorize=lambda req,s,t,e:allowed[0],current=lambda s:True,lifecycle_guard=guard)
        response=service.handle('coverage',Request('GET','/api/tasks/5/coverage'),{'id':'5'})
        self.assertEqual(response.status,403)
        self.assertNotIn(b'by_type',response.body)
