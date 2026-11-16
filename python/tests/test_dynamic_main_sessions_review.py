"""Independent final-disclosure tests for dynamic source main-session reads."""
import unittest
from unittest.mock import patch
import test_dynamic_main_sessions as fixture
from artex.server import main_sessions
from artex.server.application import build_application
from test_application import TestCodec

class MainSessionReview(unittest.TestCase):
    def setUp(self):self.f=fixture.Tests();self.f.setUp()
    def test_request_revoked_during_json_no_session_metadata(self):
        f=self.f;original=main_sessions.json_response;called=[]
        def serialize(*a,**kw):
            result=original(*a,**kw);called.append(True);f.request_grant=False;return result
        with patch.object(main_sessions,'json_response',serialize):
            status,body=f.request('/api/exploration/main-sessions','task=5')
        self.assertTrue(called);self.assertEqual(status,403);self.assertNotIn(b'2026',body)
    def test_jwt_binding_replaced_during_json_no_session_metadata(self):
        f=self.f;base=build_application(f.db,b'x'*32,TestCodec());f.app=f.router.bind(base)
        original=main_sessions.json_response;called=[]
        def serialize(*a,**kw):
            result=original(*a,**kw);called.append(True);base.router.authorize=lambda req:True;return result
        with patch.object(main_sessions,'json_response',serialize):
            status,body=f.request('/api/exploration/main-sessions','task=5')
        self.assertTrue(called);self.assertNotEqual(status,200);self.assertNotIn(b'2026',body)
    def test_task_exploration_reassigned_after_query_no_old_session(self):
        f=self.f;f.db.session_after=lambda:f.db.scopes.__setitem__(5,44)
        status,body=f.request('/api/exploration/main-sessions','task=5')
        self.assertNotEqual(status,200);self.assertNotIn(b'2026',body)
