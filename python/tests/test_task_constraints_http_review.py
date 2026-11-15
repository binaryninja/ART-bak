"""Independent explicit task-constraint HTTP policy/transaction review."""
import unittest
from dataclasses import FrozenInstanceError
import test_task_constraints_http as fixtures

class ConstraintHTTPReview(unittest.TestCase):
    setUp=fixtures.ConstraintHTTPTests.setUp
    make=fixtures.ConstraintHTTPTests.make
    call=fixtures.ConstraintHTTPTests.call
    def test_coordinated_executor_rebind_in_policy_cannot_redirect_write(self):
        foreign=fixtures.DB()
        def policy(*args):
            self.service.executor=foreign;self.store.executor=foreign
            return True
        self.router=self.make(mutation_authorize=policy)
        response=self.call('POST',{'kind':'deny','text':'boundary'})
        self.assertEqual(response.status,503)
        self.assertEqual(len(foreign.rows),1);self.assertEqual(foreign.calls,[])
    def test_delete_grant_not_satisfied_by_read_and_edit_approval(self):
        self.deletion=False
        response=self.call('DELETE',cid=1)
        self.assertEqual(response.status,403);self.assertIn(1,self.db.rows)
        self.assertEqual(self.db.calls,[])
    def test_normalized_policy_proposal_immutable(self):
        seen=[]
        def policy(req,action,binding,proposal):
            seen.append(proposal)
            with self.assertRaises(FrozenInstanceError):proposal.text='different'
            return True
        self.router=self.make(mutation_authorize=policy)
        response=self.call('POST',{'kind':' DENY ','text':'  original  '})
        self.assertEqual(response.status,200);self.assertEqual(seen[0].text,'original')
        self.assertEqual(self.db.rows[2][1],'original')
    def test_commit_failure_not_success_or_exposed_detail(self):
        from contextlib import contextmanager
        @contextmanager
        def uncertain():
            yield self.db
            raise RuntimeError('private commit detail')
        self.db.transaction=uncertain
        response=self.call('POST',{'kind':'deny','text':'boundary'})
        self.assertEqual(response.status,500);self.assertNotIn(b'private',response.body)
        self.assertIn(b'reconcile',response.body)
        self.assertEqual(len(self.db.rows),2) # outcome may have persisted, not retried
    def test_idle_callback_revision_change_prevents_mutation(self):
        def idle(binding):self.valid=False;return True
        self.router=self.make(idle=idle)
        self.assertEqual(self.call('DELETE',cid=1).status,409)
        self.assertEqual(self.db.calls,[])
