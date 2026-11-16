"""Independent read-only goal disclosure and binding boundaries."""
import unittest
from contextlib import contextmanager
from unittest.mock import patch
import test_task_goal_reads as fixture
import test_dynamic_goals as dynamic
from artex.server.task_goal_reads import TaskGoalReads
from artex.server import task_goal_reads
from artex.server.application import build_application
from test_application import TestCodec

class GoalReadReview(unittest.TestCase):
    def setUp(self):self.f=fixture.Tests();self.f.setUp()
    def test_transaction_exit_revocation_hides_goal(self):
        f=self.f
        @contextmanager
        def tx():
            yield f.db
            f.allowed=False
        f.db.transaction=tx
        response=f.request()
        self.assertEqual(response.status,403);self.assertNotIn(b'Goal for',response.body)
    def test_guard_exit_revocation_hides_goal_without_nested_guard(self):
        f=self.f;e,s,source,http,current,_=f.service._original_pins
        entered=[]
        @contextmanager
        def guard(scope):
            self.assertEqual(entered,[]);entered.append(True)
            try:yield
            finally:entered.clear();f.allowed=False
        f.service=TaskGoalReads(e,s,authorize=source,request_authorize=http,current=current,lifecycle_guard=guard)
        response=f.request()
        self.assertEqual(response.status,403);self.assertNotIn(b'Goal for',response.body)
    def test_overflow_sentinel_rejects_complete_response(self):
        f=self.f;original=f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'bounded_goals' in q.sql:
                row=list(rows[0]);row[2]=None;return [tuple(row)]
            return rows
        f.db.fetch_all=fetch
        response=f.request()
        self.assertEqual(response.status,503);self.assertNotIn(b'Goal for',response.body)
    def test_dynamic_original_auth_final_json_boundary(self):
        f=dynamic.Tests();f.setUp();self.addCleanup(f.doCleanups)
        base=build_application(f.f.db,b'x'*32,TestCodec());f.f.app=f.f.router.bind(base)
        original=task_goal_reads.json_response;called=[]
        def serialize(*a,**kw):
            result=original(*a,**kw);called.append(True);base.router.authorize=lambda req:True;return result
        with patch.object(task_goal_reads,'json_response',serialize):
            status,body=f.f.request('/api/tasks/5/goals','')
        self.assertTrue(called);self.assertNotEqual(status,200);self.assertNotIn(b'Goal 9',body)
