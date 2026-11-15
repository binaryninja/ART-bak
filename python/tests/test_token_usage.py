from contextlib import nullcontext
from decimal import Decimal
import unittest
from artex.db.token_usage import TokenUsageStore,UsageScope,UsageDenied,UsageUnavailable
from artex.server.token_usage import TokenUsageService
from artex.server.core import Request
from test_task_categories import Fake

class UsageTests(unittest.TestCase):
    def make(self,results,task=False):
        self.db=Fake(results);self.scope=UsageScope('explicit',5,9) if task else UsageScope('explicit')
        return TokenUsageStore(self.db,self.scope,authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
    def test_daily_source_utc_rolling_and_exact_integer(self):
        s=self.make([[('2026-01-01',Decimal(9007199254740993),2,3)]])
        r=s.daily(0);self.assertEqual(r[0]['input_tokens'],9007199254740993);self.assertNotIn('cache_write_tokens',r[0]);self.assertNotIn('cost',r[0])
        q=[q for _,q in self.db.calls if q][-1];self.assertEqual(q.params,(30,10001));self.assertIn("AT TIME ZONE 'UTC'",q.sql);self.assertIn("NOW()-($1*INTERVAL '1 day')",q.sql);self.assertIn("kind='result'",q.sql)
    def test_archive_and_scoped_global_failclosed(self):
        s=self.make([[(None,0,0,0)]])
        with self.assertRaises(UsageUnavailable):s.daily()
        s=self.make([],True)
        with self.assertRaises(UsageDenied):s.daily()
        self.assertEqual(self.db.calls,[])
    def test_conversations_preserve_each_row_nullable_profile(self):
        s=self.make([[(None,'2026-01-01 12:00:00+00',0,0,0,0),(3,'2026-01-01 12:00:00+00',1,2,3,4)]])
        r=s.conversations();self.assertEqual(len(r),2);self.assertIsNone(r[0]['llm_profile_id']);self.assertEqual(r[1]['cache_write_tokens'],4)
        q=[q for _,q in self.db.calls if q][0];self.assertIn('LEFT JOIN',q.sql);self.assertIn('GROUP BY c.id',q.sql)
    def test_task_workers_sessions_total_independent(self):
        s=self.make([[(5,)],[('unknown-worker',1,2,3,4)],[('main:2',1,1,0,0),('intent:7',0,1,3,4)],[(1,2,3,4)]],True)
        r=s.exploration();self.assertEqual(r['workers'][0]['worker'],'unknown-worker');self.assertEqual(r['total']['input_tokens'],1)
        qs=[q for _,q in self.db.calls if q];self.assertEqual(qs[0].params,(5,9));self.assertIn('n.exploration_id=a.exploration_id',qs[2].sql);self.assertIn('main_seg',qs[2].sql)
    def test_worker_overflow_and_bad_decimal_reject(self):
        s=self.make([[(5,)],[(None,1,2,3,4)],[],[(1,2,3,4)]],True)
        with self.assertRaises(UsageUnavailable):s.exploration()
        s=self.make([[('2026-01-01',Decimal('1.5'),0,0)]])
        with self.assertRaises(UsageUnavailable):s.daily()
    def test_scope_rebind_callback_no_sql(self):
        s=self.make([])
        def change(*a):s.executor=object();return True
        s.authorize=change
        with self.assertRaises(UsageDenied):s.conversations()
        self.assertEqual(self.db.calls,[])
    def test_http_explicit_task_no_fallback(self):
        s=self.make([],True);service=TokenUsageService(s,authorize=lambda *a:True)
        self.assertEqual(service.handle('exploration',Request('GET','/api/exploration/tokens')).status,400)
        self.assertEqual(service.handle('exploration',Request('GET','/api/exploration/tokens?task=6')).status,403)
        self.assertEqual(self.db.calls,[])
    def test_http_daily_and_bad_filters(self):
        s=self.make([[('2026-01-01',1,2,3)]]);service=TokenUsageService(s,authorize=lambda *a:True)
        self.assertEqual(service.handle('daily',Request('GET','/api/tokens/daily?days=7')).status,200)
        self.assertEqual(service.handle('daily',Request('GET','/api/tokens/daily?timezone=local')).status,400)
    def test_http_denial_before_aggregates(self):
        s=self.make([]);service=TokenUsageService(s,authorize=lambda *a:False)
        self.assertEqual(service.handle('conversations',Request('GET','/api/tokens/conversations')).status,403);self.assertEqual(self.db.calls,[])
    def test_daily_archive_sentinel_shares_the_aggregate_statement_snapshot(self):
        s=self.make([[(None,0,0,0)]])
        with self.assertRaises(UsageUnavailable):s.daily(7)
        queries=[q for _,q in self.db.calls if q]
        self.assertEqual(len(queries),1)
        self.assertIn('EXISTS(SELECT 1 FROM task_archives)',queries[0].sql)
        self.assertIn('FROM activity',queries[0].sql)
        self.assertIn('UNION ALL SELECT NULL::text',queries[0].sql)
        self.assertIn('WHERE NOT (SELECT blocked FROM archive_state)',queries[0].sql)
