import unittest
import test_task_coverage as fixtures
from artex.db.task_coverage import CoverageDenied
class CoverageDisclosureReview(unittest.TestCase):
    def test_source_grant_revoked_during_aggregate(self):
        f=fixtures.CoverageTests();live=[True]
        s=f.make([fixtures.PARENT,fixtures.SOURCES,[(1,)],[('ip',1,0)]],authorize=lambda sc,t,e: t==5 or live[0])
        original=f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'GROUP BY' in q.sql:live[0]=False
            return rows
        f.db.fetch_all=fetch
        with self.assertRaises(CoverageDenied):s.coverage()
    def test_connector_grant_revoked_during_company_read(self):
        f=fixtures.CoverageTests();live=[True]
        s=f.make([fixtures.PARENT,[],[fixtures.asset(1,'ip',company=3)],[('private company',)]],connector_authorize=lambda *a:live[0])
        original=f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'FROM companies' in q.sql:live[0]=False
            return rows
        f.db.fetch_all=fetch
        with self.assertRaises(CoverageDenied):s.coverage_graph()
