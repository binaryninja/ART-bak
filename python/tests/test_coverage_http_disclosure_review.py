"""Independent HTTP disclosure tests using inert SQL and exact selectors."""
import unittest
import test_task_coverage_http as http
import test_task_coverage as data
class CoverageHTTPDisclosureReview(unittest.TestCase):
    def test_request_grant_revoked_after_asset_reference_fetch(self):
        f=http.Tests();f.make([data.PARENT,[],[(7,'intent','done',{'text':'synthetic-private'},5)]])
        original=f.db.fetch_all
        def fetch(query):
            rows=original(query)
            if len([q for _,q in f.db.calls if q])==3:f.allowed=False
            return rows
        f.db.fetch_all=fetch
        response=f.call('asset-refs','?asset_id=3')
        self.assertEqual(response.status,403);self.assertNotIn(b'synthetic-private',response.body)
    def test_http_graph_connector_revocation_hides_joined_company(self):
        f=http.Tests();live=[True]
        f.make([data.PARENT,[],[data.asset(1,'ip',company=3)],[('synthetic-private-company',)]],connector=lambda *a:live[0])
        original=f.db.fetch_all
        def fetch(query):
            rows=original(query)
            if 'FROM companies' in query.sql:live[0]=False
            return rows
        f.db.fetch_all=fetch
        response=f.call('coverage-graph')
        self.assertEqual(response.status,403);self.assertNotIn(b'synthetic-private-company',response.body)
    def test_original_request_policy_cannot_be_replaced_during_fetch(self):
        f=http.Tests();f.make([data.PARENT,[],[(7,'intent','done',{'text':'synthetic-private'},5)]])
        original=f.db.fetch_all
        def fetch(query):
            rows=original(query)
            if len([q for _,q in f.db.calls if q])==3:
                f.allowed=False;f.service.authorize=lambda *a:True
            return rows
        f.db.fetch_all=fetch
        response=f.call('asset-refs','?asset_id=3')
        self.assertEqual(response.status,403);self.assertNotIn(b'synthetic-private',response.body)
