"""Independent literal-policy mutation ownership review, inert persistence only."""
import unittest
from artex.db.tool_intercept_config import PolicyMutation,ToolRuleProposal,PolicyDenied,Query
from test_tool_intercept_config import fixture,NOW

class ToolPolicyReview(unittest.TestCase):
    def test_final_grant_cannot_change_approved_proposal(self):
        db,s,ic,o=fixture([[(4,NOW,NOW)]]);m=PolicyMutation('create',proposal=ToolRuleProposal('original',pattern='Bash'))
        calls=[]
        def grant(value):
            calls.append(1)
            if len(calls)==2:object.__setattr__(value.proposal,'pattern','Edit')
            return True
        with self.assertRaises(PolicyDenied):o.mutate(m,request_authorize=grant)
        self.assertFalse(any(isinstance(q,Query) and q.sql.startswith('INSERT') for q in db.calls))
    def test_new_pending_approval_in_final_grant_blocks_effect(self):
        db,s,ic,o=fixture([[(4,NOW,NOW)]]);m=PolicyMutation('create',proposal=ToolRuleProposal('original',pattern='Bash'))
        calls=[]
        def grant(value):
            calls.append(1)
            if len(calls)==2:ic._pending[8]=object()
            return True
        with self.assertRaises(PolicyDenied):o.mutate(m,request_authorize=grant)
        self.assertFalse(any(isinstance(q,Query) and q.sql.startswith('INSERT') for q in db.calls))
    def test_all_declared_actual_caches_invalidated_only_after_commit(self):
        from contextlib import nullcontext
        from artex.intercept import Interceptor
        from artex.intercept.policy_owner import InterceptPolicyOwner
        from artex.db.tool_intercept_config import ToolInterceptConfigStore
        from test_tool_intercept_config import DB
        db=DB([[(4,NOW,NOW)]]);s=ToolInterceptConfigStore(db);ics=(Interceptor(s),Interceptor(s))
        for ic in ics:ic._rules=['old'];ic._tools={'old'}
        owner=InterceptPolicyOwner(s,ics,workspace='fixture',authorize=lambda m:True,current=lambda:True,idle=lambda xs:xs is ics,lifecycle_guard=lambda:nullcontext())
        def query(q):
            if q.sql.startswith('INSERT'):
                self.assertTrue(all(ic._tools=={'old'} for ic in ics))
        db.on_query=query
        owner.mutate(PolicyMutation('create',proposal=ToolRuleProposal('x',pattern='Bash')),request_authorize=lambda m:True)
        self.assertIn('commit',db.calls);self.assertEqual(owner.generation,1)
        self.assertTrue(all(ic._rules is None and ic._tools is None for ic in ics))
    def test_replaced_invalidation_method_refused_before_transaction(self):
        db,s,ic,o=fixture();ic.invalidate=lambda:None
        with self.assertRaises(PolicyDenied):o.mutate(PolicyMutation('create',proposal=ToolRuleProposal('x',pattern='Bash')),request_authorize=lambda m:True)
        self.assertFalse(db.calls)
    def test_guard_exit_failure_after_commit_quarantines_no_retry(self):
        from contextlib import contextmanager
        from artex.db.tool_intercept_config import PolicyUncertain
        @contextmanager
        def guard():
            yield
            raise RuntimeError('inert postcommit exit')
        db,s,ic,o=fixture([[(4,NOW,NOW)]],guard=guard)
        m=PolicyMutation('create',proposal=ToolRuleProposal('x',pattern='Bash'))
        with self.assertRaises(PolicyUncertain):o.mutate(m,request_authorize=lambda m:True)
        self.assertIn('commit',db.calls);before=len(db.calls)
        with self.assertRaises(PolicyUncertain):o.mutate(m,request_authorize=lambda m:True)
        self.assertEqual(len(db.calls),before);self.assertIsNone(ic._rules);self.assertIsNone(ic._tools)
        self.assertEqual(ic.match('Bash',{})[0].action,'deny')
