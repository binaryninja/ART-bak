"""Independent metadata-authority tests with injected transactions only."""
from contextlib import nullcontext
import unittest
import test_visibility as fixture
from artex.db.visibility import VisibilityStore, VisibilityScope, VisibilitySelection, VisibilityUnavailable


class Review(unittest.TestCase):
    def make(self):
        db=fixture.DB()
        store=VisibilityStore(db,VisibilityScope('catalog','r'),authorize=lambda *a:True,
            mutation_authorize=lambda *a:True,current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
        return db,store

    def test_guard_rebound_by_policy_rejected_before_read(self):
        db,store=self.make()
        def policy(*args):
            store.guard=lambda scope:nullcontext()
            return True
        with self.assertRaises(VisibilityUnavailable):
            store.read(VisibilitySelection('mcp',3),request_authorize=policy)
        self.assertEqual(db.calls,[])

    def test_guard_rebound_by_current_rejected_before_write(self):
        db,store=self.make()
        def current(scope):
            store.guard=lambda scope:nullcontext()
            return True
        store.current=current
        with self.assertRaises(VisibilityUnavailable):
            store.toggle(VisibilitySelection('skill',skill_name='demo',agent_id=2,visible=True),request_authorize=lambda *a:True)
        self.assertEqual(db.calls,[])
        self.assertEqual(db.writes,[])

    def test_source_mcp_disable_removes_every_tool_row_only_for_exact_resource(self):
        db,store=self.make()
        store.toggle(VisibilitySelection('mcp',7,agent_id=2,visible=False),request_authorize=lambda *a:True)
        self.assertEqual(len(db.writes),1)
        q=db.writes[0]
        self.assertEqual(q.params,(2,'mcp',7))
        self.assertNotIn('mcp_tool_name',q.sql)

    def test_commit_failure_is_uncertain_not_success(self):
        from contextlib import contextmanager
        from artex.db.visibility import VisibilityUncertain
        db,store=self.make()
        @contextmanager
        def fail_commit():
            yield db
            raise RuntimeError('private commit failure')
        db.transaction=fail_commit
        with self.assertRaises(VisibilityUncertain) as caught:
            store.toggle(VisibilitySelection('skill',skill_name='demo',agent_id=2,visible=True),request_authorize=lambda *a:True)
        self.assertNotIn('private',str(caught.exception))
