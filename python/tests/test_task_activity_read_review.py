"""Independent activity disclosure and cursor checks; inert SQL only."""
import unittest
from unittest.mock import patch
from test_task_activity_read import Tests
from artex.server.streaming import ClosingIterator
from artex.server import task_activity
class ActivityReadReview(unittest.TestCase):
    def fixture(self):
        f=Tests();f.setUp();return f
    def test_serialization_revocation_hides_page(self):
        f=self.fixture();encode=task_activity.json_response
        def revoked(*a,**kw):
            result=encode(*a,**kw);f.http_allowed=False;return result
        with patch.object(task_activity,'json_response',revoked):
            response=f.request('list','/api/exploration/activity?task=5')
        self.assertEqual(response.status,403);self.assertNotIn(b'private-1',response.body)
    def test_stream_frame_revocation_drops_buffer(self):
        f=self.fixture();frame=task_activity.sse_frame
        response=f.request('stream','/api/exploration/activity/stream?task=5')
        def revoked(*a,**kw):
            result=frame(*a,**kw);f.allowed=False;return result
        with patch.object(task_activity,'sse_frame',revoked):
            iterator=ClosingIterator(response);self.assertEqual(list(iterator),[])
        self.assertTrue(iterator.failed);self.assertEqual(response.body.pending,[])
    def test_future_cursor_never_rewinds(self):
        f=self.fixture();r=f.request('list','/api/exploration/activity?task=5&since=100')
        self.assertEqual(r.status,200);self.assertEqual(r.json(),{'items':[],'cursor':100})
    def test_strict_local_intent_does_not_accept_source(self):
        f=self.fixture();r=f.request('history','/api/exploration/activity/history?task=5&session=intent:99&limit=2')
        self.assertEqual(r.status,404)
        self.assertFalse(any('WITH snap' in q.sql for q in f.db.queries))
    def test_later_batch_revoke_during_frame_blocks_that_frame(self):
        f=self.fixture();allowed=[True]
        f.store.raw_authorize=lambda scope,selection:selection.since==0 or allowed[0]
        response=f.request('stream','/api/exploration/activity/stream?task=5');iterator=ClosingIterator(response)
        next(iterator);next(iterator)
        original=task_activity.sse_frame
        def revoke(*a,**kw):
            result=original(*a,**kw);allowed[0]=False;return result
        with patch.object(task_activity,'sse_frame',revoke):self.assertEqual(list(iterator),[])
        self.assertTrue(iterator.failed);self.assertEqual(response.body.pending,[])
    def test_factory_rejects_pre_service_guard_rebinding(self):
        from artex.server.application import build_application,build_persisted_application
        from artex.server.task_activity import TaskActivityService
        from test_application import TestCodec
        from contextlib import nullcontext
        for factory in (build_application,build_persisted_application):
            f=self.fixture();f.store.guard=lambda *a:nullcontext()
            service=TaskActivityService(f.store,authorize=lambda *a:True,stream_current=lambda *a:True)
            with self.assertRaises(ValueError):factory(f.db,b'x'*32,TestCodec(),task_activity_service=service)
            self.assertEqual(f.db.queries,[])
