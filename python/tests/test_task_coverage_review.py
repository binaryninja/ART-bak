"""Independent coverage resource and scope review; no database execution."""
import unittest
from artex.db.task_coverage import CoverageDenied,CoverageUnavailable
import test_task_coverage as fixture

class CoverageReview(unittest.TestCase):
    def setUp(self):self.f=fixture.CoverageTests()
    def queries(self):return [q for _,q in self.f.db.calls if q]
    def test_asset_reference_sql_bounds_projection_before_transfer(self):
        store=self.f.make([fixture.PARENT,[],[(1,'fact','confirmed',{'summary':'small'},5)]],max_bytes=1024)
        store.asset_refs(7);query=self.queries()[-1]
        self.assertIn('octet_length',query.sql)
        self.assertIn('CASE',query.sql)
        self.assertNotIn('en.state,en.payload,',query.sql)
    def test_untested_sql_bounds_labels_before_transfer(self):
        store=self.f.make([fixture.PARENT,[],[(1,)],[(7,'ip','192.0.2.1')]],max_bytes=1024)
        store.untested();query=self.queries()[-1]
        self.assertIn('octet_length',query.sql);self.assertIn('CASE',query.sql)
    def test_graph_and_company_projection_bounded_before_transfer(self):
        store=self.f.make([fixture.PARENT,[],[fixture.asset(1,'ip',ip='192.0.2.1',company=3)],[('Company',)]],max_bytes=1024)
        store.coverage_graph();queries=self.queries()
        assets=next(q for q in queries if 'FROM assets a JOIN target' in q.sql)
        company=next(q for q in queries if 'FROM companies' in q.sql)
        self.assertIn('octet_length',assets.sql);self.assertIn('CASE',assets.sql)
        self.assertIn('octet_length',company.sql);self.assertIn('CASE',company.sql)
    def test_authorization_callback_executor_rebind_denied(self):
        store=self.f.make([fixture.PARENT,[]]);db=self.f.db
        def authorize(*args):store.executor=object();return True
        store.authorize=authorize
        with self.assertRaises(CoverageDenied):store.coverage()
        self.assertEqual([q for _,q in db.calls if q],[])
    def test_scope_snapshot_authority_cannot_be_relabelled(self):
        from dataclasses import replace
        store=self.f.make([fixture.PARENT,[]])
        store.scope=replace(store.scope,task_id=6)
        with self.assertRaises(CoverageDenied):store.asset_refs(7)
        self.assertEqual(self.queries(),[])
    def test_disabled_coverage_does_not_enable_global_fallback(self):
        store=self.f.make([[(5,9,False)],[]])
        self.assertEqual(store.coverage()['denominator'],0)
        self.assertEqual(len(self.queries()),2)
