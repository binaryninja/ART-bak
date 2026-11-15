"""Independent source normalization and scoped metadata HTTP review."""
import unittest
from dataclasses import FrozenInstanceError
from artex.server.intercept_rules import proposal
import test_intercept_rules_http as fixtures

class Review(unittest.TestCase):
    make=fixtures.HTTPRuleTests.make
    call=fixtures.HTTPRuleTests.call
    def test_normalized_proposal_is_frozen_and_before_both_grants(self):
        self.make([[(5,)],[(7,)],[fixtures.ROW]])
        seen=[]
        def approve(request,scope,selection):
            seen.append(selection.proposal.pattern)
            with self.assertRaises(FrozenInstanceError):selection.proposal.pattern='broader'
            return True
        self.service.authorize=approve;self.service.mutate=approve
        result=self.call('create',{'kind':'exact_ip','pattern':'\u2003192.0.2.1\u2003'})
        self.assertEqual(result.status,200);self.assertEqual(seen,['192.0.2.1']*2)
        insert=next(q for _,q in self.f.db.calls if q and q.sql.startswith('INSERT'))
        self.assertIn('192.0.2.1',insert.params)
    def test_scope_swap_during_request_authority_prevents_sql(self):
        self.make([])
        def authorize(*args):
            from artex.db.intercept_rules import RuleScope
            self.store.scope=RuleScope('other',5,9);return True
        self.service.authorize=authorize
        self.assertEqual(self.call('list').status,403);self.assertEqual(self.f.db.calls,[])
    def test_stale_cas_no_execute_or_false_commit(self):
        self.make([[(5,)],[fixtures.ROW]])
        from artex.server.core import Request
        response=self.service.handle('delete',Request('DELETE','/api/tasks/5/intercept-rules/7',{'X-Rule-Updated-At':'2000-01-01T00:00:00Z'}),{'id':'5','rid':'7'})
        self.assertEqual(response.status,409);self.assertFalse(any(m=='execute' for m,_ in self.f.db.calls))
    def test_go_whitespace_not_python_extra_c0(self):
        # Source TrimSpace leaves U+001C unchanged for free-form exact_domain.
        self.assertEqual(proposal({'kind':'exact_domain','pattern':'\x1c x \x1c'},True).pattern,'\x1c x \x1c')
        self.assertEqual(proposal({'kind':'cidr','pattern':'2001:db8::1/64'},True).pattern,'2001:db8::1/64')
    def test_response_oversize_after_write_reports_commit(self):
        row=list(fixtures.ROW);row[4]='x'*2000
        self.make([[(5,)],[(7,)],[row]]);self.service.max_response_bytes=1024
        result=self.call('create',{'kind':'exact_domain','pattern':'a.test'})
        self.assertEqual(result.status,503);self.assertIs(result.json()['committed'],True)
        self.assertEqual(self.f.db.calls[-1][0],'commit')

if __name__=='__main__':unittest.main()
