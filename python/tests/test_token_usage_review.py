"""Independent bounded token reads; inert SQL fixture only."""
import unittest
from decimal import Decimal
from contextlib import nullcontext
from artex.db.token_usage import TokenUsageStore, UsageScope, UsageUnavailable
from artex.server.token_usage import TokenUsageService
from artex.server.core import Request
from test_task_categories import Fake

class TokenUsageReview(unittest.TestCase):
    def make(self, results, **kwargs):
        self.db = Fake(results)
        return TokenUsageStore(self.db, UsageScope('fixed'), authorize=lambda *a: True,
            current=lambda *a: True, lifecycle_guard=lambda *a: nullcontext(), **kwargs)

    def test_oversized_group_count_is_not_partial_success(self):
        s=self.make([( [(None,'date',1,2,3,4)]*2 )],max_rows=1)
        with self.assertRaises(UsageUnavailable): s.conversations()
        query=[q for _,q in self.db.calls if q][-1]
        self.assertEqual(query.params,(2,))

    def test_invalid_numeric_aggregates_never_float_coerce(self):
        for value in (True,1.0,Decimal('NaN'),Decimal('Infinity'),Decimal('0.1'),-1,2**63):
            with self.subTest(value=value):
                s=self.make([[(None,'date',value,0,0,0)]])
                with self.assertRaises(UsageUnavailable): s.conversations()

    def test_guard_rebind_during_http_authorization_blocks_sql(self):
        s=self.make([])
        def authorize(*args):
            s.guard=lambda *a: nullcontext()
            return True
        service=TokenUsageService(s,authorize=authorize)
        self.assertEqual(service.handle('conversations',Request('GET','/api/tokens/conversations')).status,403)
        self.assertEqual(self.db.calls,[])

    def test_query_duplicates_and_unknown_filters_never_read(self):
        s=self.make([]);service=TokenUsageService(s,authorize=lambda *a: True)
        for query in ('days=2&days=3','days=2&task=5','days=3661','days=1.5'):
            with self.subTest(query=query):
                self.assertEqual(service.handle('daily',Request('GET','/api/tokens/daily?'+query)).status,400)
        self.assertEqual(self.db.calls,[])
