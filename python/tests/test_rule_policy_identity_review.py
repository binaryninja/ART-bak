"""Original read policy identity cannot be replaced during a snapshot."""
import unittest
import test_intercept_rules_http as fixture
class RulePolicyIdentityReview(unittest.TestCase):
    def test_request_policy_swap_cannot_hide_original_revocation(self):
        f=fixture.HTTPRuleTests();f.make([[(5,)],[fixture.ROW]])
        live=[True];f.service.authorize=lambda *a:live[0]
        original=f.f.db.fetch_all
        def fetch(query):
            rows=original(query)
            if 'WITH selected' in query.sql:
                live[0]=False;f.service.authorize=lambda *a:True
            return rows
        f.f.db.fetch_all=fetch
        response=f.call('list')
        self.assertEqual(response.status,403);self.assertNotIn(b'example.test',response.body)
