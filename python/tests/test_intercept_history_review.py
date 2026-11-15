"""Independent read-only inspection regressions with synthetic SQL rows."""
import unittest
import test_intercept_history as f
from artex.server.core import Request

class InterceptHistoryReview(unittest.TestCase):
    def test_foreign_task_row_never_returns_content(self):
        fixture=f.InspectionTests();row=list(f.ROW);row[4]='foreign-task';row[9]='SYNTHETIC_PRIVATE_REASON'
        fixture.make([[row]])
        response=fixture.service.handle('pending',Request('GET','/api/intercept/pending'))
        self.assertEqual(response.status,503);self.assertNotIn(b'SYNTHETIC_PRIVATE_REASON',response.body)
    def test_pending_overflow_not_truncated(self):
        fixture=f.InspectionTests();fixture.make([[f.ROW,f.ROW]])
        fixture.store.max_rows=1
        response=fixture.service.handle('pending',Request('GET','/api/intercept/pending'))
        self.assertEqual(response.status,503)
        self.assertEqual(fixture.db.calls[0][1].params[4],2)
    def test_scope_or_guard_changed_by_raw_callback_denies_without_read(self):
        for field in ('scope','guard'):
            fixture=f.InspectionTests();fixture.make([])
            def raw(*a):setattr(fixture.store,field,object());return True
            fixture.store.raw=raw
            self.assertEqual(fixture.service.handle('pending',Request('GET','/api/intercept/pending')).status,403)
            self.assertEqual(fixture.db.calls,[])
    def test_history_bounds_join_labels_and_raw_json_before_transfer(self):
        fixture=f.InspectionTests();fixture.make([[f.ROW]])
        response=fixture.service.handle('history',Request('GET','/api/intercept/history?page=1'))
        self.assertEqual(response.status,200);sql=fixture.db.calls[0][1].sql
        for field in ('tool_input','reason','conv_title','conv_agent_key','rule_name'):
            self.assertIn('octet_length('+field+'::text)',sql)
            self.assertIn('CASE WHEN bytes<=$7 THEN b.'+field+' ELSE NULL END',sql)
        self.assertIn('c.id=ip.conversation_id',sql);self.assertIn('ir.id=ip.rule_id',sql)
        self.assertNotIn('ip.audit',sql)
