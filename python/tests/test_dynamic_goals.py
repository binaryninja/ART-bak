"""Two resolved overview goal reads, no mutations or provider calls."""
import unittest
import test_dynamic_main_sessions as fixtures
from test_task_goals_http import node
from artex.server.task_read_builder import TaskReadBuilder
class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        db=self.f.db;old=db.fetch_all
        def fetch(q):
            if 'bounded_goals' in q.sql:
                db.queries.append(q);return [(*node(q.params[0],'goal',{'text':f'Goal {q.params[0]}'},state='met'),1)]
            return old(q)
        db.fetch_all=fetch
        b=self.f.builder
        self.f.router.builder=TaskReadBuilder(db,request_authorize=b._policies[0],context_authorize=b._policies[1],
            asset_authorize=b._policies[2],runtime_snapshot=b._policies[3],activity_authorize=b._policies[4],
            activity_raw_authorize=b._policies[5],activity_request_authorize=b._policies[6],stream_authorize=b._policies[7],
            goal_authorize=lambda s:True,goal_request_authorize=lambda req,s:True)
        from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
        from artex.server.application import build_application
        from test_application import TestCodec
        oldrouter=self.f.router
        self.f.router=TaskRuntimeReadRouter(oldrouter.tasks,oldrouter.builder,revision='r1',authorize_selector=oldrouter.authorize_selector,current=oldrouter.current,lifecycle_guard=oldrouter.guard)
        self.f.app=self.f.router.bind(build_application(db,b'x'*32,TestCodec()))
    def test_two_tasks_exact_overview_goal_shape(self):
        import json
        for task,exp in ((5,9),(6,10)):
            status,body=self.f.request(f'/api/tasks/{task}/goals','')
            self.assertEqual(status,200);goal=json.loads(body)['goals'][0]
            self.assertEqual(goal['text'],f'Goal {exp}');self.assertEqual(goal['state'],'met')
        self.assertEqual([q.params[0] for q in self.f.db.queries if 'bounded_goals' in q.sql],[9,10])
    def test_no_query_retarget_and_auth_before_lookup(self):
        self.assertEqual(self.f.request('/api/tasks/5/goals','',False)[0],401)
        self.assertEqual(self.f.request('/api/tasks/5/goals','task=6')[0],400)
        self.assertEqual(self.f.lookups,[])
