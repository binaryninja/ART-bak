import unittest
from contextlib import nullcontext
from datetime import datetime,timezone
from artex.db.intercept_history import *
from artex.server.intercept_inspection import InterceptInspectionService
from artex.server.core import Request
from test_task_categories import Fake
NOW=datetime(2026,11,15,tzinfo=timezone.utc)
ROW=(1,7,None,None,'task-text','agent','fixture',{'x':'inert'},'pending','reason',None,NOW,'unknown','','','')
class InspectionTests(unittest.TestCase):
    def make(self,results,task='task-text',raw=True):
        self.db=Fake(results);self.store=InterceptHistoryStore(self.db,InspectionScope('workspace',task),authorize=lambda *a:True,raw_authorize=lambda *a:raw,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        self.service=InterceptInspectionService(self.store,authorize=lambda *a:True)
    def test_pending_actual_bound_scope_and_json(self):
        self.make([[ROW]]);r=self.service.handle('pending',Request('GET','/api/intercept/pending'))
        self.assertEqual(r.status,200);self.assertEqual(r.json()['pending'][0]['tool_input'],{'x':'inert'})
        q=self.db.calls[0][1];self.assertEqual(q.params[0],'task-text');self.assertEqual(q.params[2],'pending');self.assertIn('CASE WHEN bytes<=',q.sql)
    def test_paginated_count_same_statement_legacy_prefix(self):
        self.make([[ROW]]);r=self.service.handle('history',Request('GET','/api/intercept/history?page=1&status=pending'))
        self.assertEqual(r.status,200);self.assertEqual(r.json()['total'],1)
        sql=self.db.calls[0][1].sql;self.assertIn('SELECT count(*) FROM filtered',sql);self.assertIn("U&'[\\6a21\\578b]%'",sql)
    def test_missing_detail_no_rows_driver_exception(self):
        self.make([[(0,)+(None,)*15]])
        self.assertEqual(self.service.handle('one',Request('GET','/api/intercept/pending/7'),'7').status,404)
    def test_raw_grant_required_before_sql(self):
        self.make([],raw=False);self.assertEqual(self.service.handle('pending',Request('GET','/api/intercept/pending')).status,403);self.assertEqual(self.db.calls,[])
    def test_overflow_rejects_not_truncate(self):
        self.make([[(1,)+ROW[1:5]+(None,)+ROW[6:]]])
        self.assertEqual(self.service.handle('pending',Request('GET','/api/intercept/pending')).status,503)
    def test_no_mutation_or_execution_and_legacy_explicit_unavailable(self):
        self.make([]);self.assertEqual(self.service.handle('history',Request('GET','/api/intercept/history')).status,503)
        self.assertEqual(self.db.calls,[])
    def test_callback_rebind_before_sql(self):
        self.make([])
        def raw(*a):self.store.executor=Fake([]);return True
        self.store.raw=raw
        self.assertEqual(self.service.handle('pending',Request('GET','/api/intercept/pending')).status,403);self.assertEqual(self.db.calls,[])
    def test_error_text_redacted(self):
        self.make([RuntimeError('private fixture')]);r=self.service.handle('pending',Request('GET','/api/intercept/pending'));self.assertEqual(r.status,503);self.assertNotIn(b'private',r.body)

class RevocationTests(InspectionTests):
    def test_raw_revocation_after_sql_blocks_private_result(self):
        self.make([[ROW]]);allowed=[True];fetch=self.db.fetch_all
        def revoked(q):
            rows=fetch(q);allowed[0]=False;return rows
        self.db.fetch_all=revoked;self.store.raw=lambda *a:allowed[0]
        response=self.service.handle('pending',Request('GET','/api/intercept/pending'))
        self.assertEqual(response.status,403);self.assertNotIn(b'reason',response.body)
