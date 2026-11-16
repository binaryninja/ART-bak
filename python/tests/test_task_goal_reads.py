import unittest
from contextlib import contextmanager,nullcontext
from unittest.mock import patch
from artex.server.task_goal_reads import TaskGoalReads
from artex.server.task_runtime_reads import TaskReadScope
from artex.server.core import Request
from test_task_goals_http import node
class DB:
    def __init__(self):self.calls=[];self.after=lambda:None
    @contextmanager
    def transaction(self):yield self
    def fetch_all(self,q):
        self.calls.append(q)
        if q.sql.startswith('SELECT id FROM tasks'):return [(q.params[0],)]
        self.after();return [(*node(2,'goal',{'text':f'Goal for exploration {q.params[0]}'},state='met'),1)]
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.allowed=True
        self.service=TaskGoalReads(self.db,TaskReadScope(5,9,'r'),authorize=lambda s:True,
            request_authorize=lambda req,s:self.allowed,current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
    def request(self):return self.service.handle(Request('GET','/api/tasks/5/goals',{}))
    def test_source_shape_and_scoped_bounded_projection(self):
        r=self.request();self.assertEqual(r.status,200)
        self.assertEqual(r.json()['goals'][0]['state'],'met')
        self.assertIn('9',r.json()['goals'][0]['text'])
        self.assertIn('COUNT(*) OVER',self.db.calls[-1].sql)
    def test_revocation_after_actual_serialization_hides_response(self):
        from artex.server import task_goal_reads
        original=task_goal_reads.json_response;called=[]
        def revoke(*args):
            r=original(*args);called.append(True);self.allowed=False;return r
        with patch.object(task_goal_reads,'json_response',revoke):r=self.request()
        self.assertTrue(called);self.assertEqual(r.status,403)
        self.assertNotIn(b'Goal for',r.body)
    def test_denial_before_sql_and_original_selector(self):
        self.allowed=False;self.assertEqual(self.request().status,403);self.assertEqual(self.db.calls,[])
        for target in ('/api/tasks/6/goals','/api/tasks/05/goals','/api/tasks/5/goals?task=6'):
            self.assertEqual(self.service.handle(Request('GET',target,{})).status,400)
    def test_scope_change_and_overflow_refused(self):
        self.db.after=lambda:object.__setattr__(self.service._original_pins[1],'exploration_id',10)
        self.assertEqual(self.request().status,403)
