import unittest
from contextlib import contextmanager,nullcontext
from datetime import datetime,timezone
from unittest.mock import patch
from artex.db.main_sessions import MainSessionsStore
from artex.db.task_coverage import CoverageScope
from artex.server.main_sessions import MainSessionsService
from artex.server.core import Request
from test_tasks_store import Fake
NOW=datetime(2026,11,15,tzinfo=timezone.utc)
class Tests(unittest.TestCase):
    def make(self,rows):
        self.db=Fake(rows);self.live=[True];self.request_live=[True]
        self.store=MainSessionsStore(self.db,CoverageScope(5,9,'r'),authorize=lambda *a:self.live[0],current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        self.service=MainSessionsService(self.store,authorize=lambda *a:self.request_live[0])
    def call(self,target='/api/exploration/main-sessions?task=5'):return self.service.handle(Request('GET',target))
    def test_source_shape_and_implicit_zero(self):
        self.make([[(5,)],[(3,NOW),(1,NOW)]]);r=self.call();self.assertEqual(r.status,200)
        self.assertEqual(r.json()['current'],3);self.assertEqual(r.json()['sessions'][-1],{'seq':0,'created_at':'0001-01-01T00:00:00Z'})
        self.assertEqual(self.db.calls[2][1].params,(9,1001))
    def test_empty_original(self):
        self.make([[(5,)],[]]);self.assertEqual(self.call().json()['current'],0)
    def test_absent_parent_archive_fails(self):
        self.make([[]]);self.assertEqual(self.call().status,503)
    def test_no_task_fallback(self):
        for target in ('/api/exploration/main-sessions','/api/exploration/main-sessions?task=6'):
            self.make([]);self.assertIn(self.call(target).status,(400,403));self.assertEqual(self.db.calls,[])
    def test_query_revocation_and_swap(self):
        for swap in (False,True):
            self.make([[(5,)],[(1,NOW)]]);fetch=self.db.fetch_all
            def read(q):
                rows=fetch(q)
                if 'main_sessions' in q.sql:
                    self.live[0]=False
                    if swap:self.store.authorize=lambda *a:True
                return rows
            self.db.fetch_all=read;self.assertEqual(self.call().status,403)
    def test_serialization_request_revocation(self):
        self.make([[(5,)],[(1,NOW)]])
        from artex.server import main_sessions as module
        original=module.json_response
        def encode(*a,**kw):
            result=original(*a,**kw);self.request_live[0]=False;return result
        with patch.object(module,'json_response',encode):self.assertEqual(self.call().status,403)
    def test_nonreentrant_guard(self):
        self.make([[(5,)],[]]);active=[False]
        @contextmanager
        def guard(scope):
            if active[0]:raise AssertionError('nested')
            active[0]=True
            try:yield
            finally:active[0]=False
        self.store.guard=self.store._guard=guard;self.service=MainSessionsService(self.store,authorize=lambda *a:True)
        self.assertEqual(self.call().status,200)
    def test_row_overflow_not_truncated(self):
        self.make([[(5,)],[(2,NOW),(1,NOW)]]);self.store.max_rows=1;self.assertEqual(self.call().status,503)
