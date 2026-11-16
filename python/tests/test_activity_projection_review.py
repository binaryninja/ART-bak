"""Independent projection receipt checks using inert SQL and temporary JSONL."""
import unittest
from artex.agent.runtime import Grant
from test_activity_projection import Tests
class ProjectionReview(unittest.TestCase):
    def fixture(self):
        f=Tests();f.setUp();self.addCleanup(f.tmp.cleanup);return f
    def test_authorizer_cannot_mutate_receipt_key_to_duplicate_usage(self):
        f=self.fixture();calls=[0]
        def authorize(proposal):
            calls[0]+=1
            f.identity['injected_nonce']=calls[0]
            return Grant(True,True,True)
        # Configure before construction so callback identity remains stable.
        from artex.agent.activity_projection import ActivityProjector
        from contextlib import nullcontext
        f.p=ActivityProjector(f.db,f.scope,transcript=f.t,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext(),authorize=authorize)
        # The caller's original scope dictionary is an input, not a live capability.
        original=dict(f.identity)
        first=f.project();f.identity=original.copy();second=f.project()
        self.assertEqual(first,second);self.assertEqual(len(f.db.rows),1)
