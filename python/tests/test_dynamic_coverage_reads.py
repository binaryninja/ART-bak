import unittest
from contextlib import nullcontext
from unittest.mock import patch
from artex.server.task_coverage_reads import TaskCoverageReads
from artex.server.task_runtime_reads import TaskReadScope
from artex.server.core import Request
from test_tasks_store import Fake
from test_task_coverage import asset
class Tests(unittest.TestCase):
    def make(self,rows,task=5,exp=9):
        self.db=Fake(rows);self.allowed=True;self.ids=[]
        def disclose(s,selection,ids):self.ids.append((s.task_id,selection,ids));return self.allowed
        return TaskCoverageReads(self.db,TaskReadScope(task,exp,'r'),authorize=lambda *a:True,
            request_authorize=lambda *a:True,asset_authorize=disclose,current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
    def test_graph_then_selected_refs_two_scopes(self):
        for task,exp in ((5,9),(6,10)):
            svc=self.make([[(task,exp,True)],[],[asset(3,'ip',ip='192.0.2.1')]],task,exp)
            r=svc.handle('graph',Request('GET',f'/api/tasks/{task}/coverage-graph',{}))
            self.assertEqual(r.status,200);self.assertEqual(r.json()['nodes'][0]['asset_id'],3)
            self.assertEqual(self.ids[-1][0],task);self.assertEqual(self.ids[-1][2],(3,))
            svc=self.make([[(task,exp,True)],[],[(7,'intent','done',{'text':'local'},task)]],task,exp)
            r=svc.handle('refs',Request('GET',f'/api/tasks/{task}/asset-refs?asset_id=3',{}))
            self.assertEqual(r.status,200);self.assertEqual(r.json()['intents'][0]['id'],7)
            self.assertEqual(self.ids[0][2],(3,));self.assertEqual(self.ids[-1][2],(3,))
    def test_asset_denial_after_graph_projection_withholds_data(self):
        svc=self.make([[(5,9,True)],[],[asset(3,'ip',ip='192.0.2.1')]])
        from artex.server import task_coverage
        original=task_coverage.json_response;calls=[]
        def revoke(*args):
            value=original(*args);calls.append(True);self.allowed=False;return value
        with patch.object(task_coverage,'json_response',revoke):r=svc.handle('graph',Request('GET','/api/tasks/5/coverage-graph',{}))
        self.assertTrue(calls);self.assertEqual(r.status,403);self.assertNotIn(b'192.0.2.1',r.body)
    def test_ref_asset_denial_before_sql_and_query_validation(self):
        svc=self.make([]);self.allowed=False
        self.assertEqual(svc.handle('refs',Request('GET','/api/tasks/5/asset-refs?asset_id=3',{})).status,403)
        self.assertEqual(self.db.calls,[])
        for target in ('/api/tasks/6/coverage-graph','/api/tasks/5/coverage-graph?task=6'):
            self.assertEqual(svc.handle('graph',Request('GET',target,{})).status,400)
    def test_no_connector_grant_inferred(self):
        svc=self.make([])
        self.assertIsNone(svc._original[7])

class DynamicTests(unittest.TestCase):
    def test_two_tasks_one_authenticated_app_graph_then_refs(self):
        import json
        import test_task_runtime_read_router as fixtures
        from artex.server.task_read_builder import TaskReadBuilder
        from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
        from artex.server.application import build_application
        from test_application import TestCodec
        f=fixtures.Tests();f.setUp();self.addCleanup(f.doCleanups)
        db=f.db;original=db.fetch_all;pending=[]
        def fetch(q):
            if q.sql.startswith('SELECT exploration_id FROM tasks'):return original(q)
            if not pending:raise AssertionError(q.sql)
            return pending.pop(0)
        db.fetch_all=fetch
        b=TaskReadBuilder(db,request_authorize=lambda *a:True,context_authorize=lambda *a:True,asset_authorize=lambda *a:True,
            runtime_snapshot=lambda *a:None,activity_authorize=lambda *a:True,activity_raw_authorize=lambda *a:True,
            activity_request_authorize=lambda *a:True,stream_authorize=lambda *a:True,
            coverage_authorize=lambda *a:True,coverage_request_authorize=lambda *a:True,coverage_asset_authorize=lambda *a:True)
        r=TaskRuntimeReadRouter(f.tasks,b,revision='r',authorize_selector=lambda *a:True,current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
        f.app=r.bind(build_application(db,b'x'*32,TestCodec()))
        from contextlib import contextmanager
        @contextmanager
        def transaction():yield db
        db.transaction=transaction
        for task,exp in ((5,9),(6,10)):
            pending[:]=[[(task,exp,True)],[],[asset(task,'ip',ip='192.0.2.1')]]
            status,data=f.request(f'/api/tasks/{task}/coverage-graph')
            self.assertEqual(status,200);self.assertEqual(data['nodes'][0]['asset_id'],task);self.assertFalse(pending)
            pending[:]=[[(task,exp,True)],[],[]]
            status,data=f.request(f'/api/tasks/{task}/asset-refs',f'asset_id={task}')
            self.assertEqual(status,200);self.assertEqual(data,{'intents':[],'facts':[],'findings':[]});self.assertFalse(pending)

class ConnectorRevocationTests(unittest.TestCase):
    def test_final_asset_callback_revokes_company_connector(self):
        allowed=[True];seen=[]
        db=Fake([[(5,9,True)],[],[asset(3,'ip',company=7,ip='192.0.2.1')],[('Private company',)]])
        def connector(scope,kind,value):seen.append((kind,value));return allowed[0]
        def asset_grant(scope,selection,ids):
            self.assertEqual(ids,(3,));allowed[0]=False;return True
        service=TaskCoverageReads(db,TaskReadScope(5,9,'r'),authorize=lambda *a:True,
            request_authorize=lambda *a:True,asset_authorize=asset_grant,current=lambda s:True,
            lifecycle_guard=lambda s:nullcontext(),connector_authorize=connector)
        result=service.handle('graph',Request('GET','/api/tasks/5/coverage-graph'))
        self.assertIn(('company',7),seen)
        self.assertEqual(result.status,403);self.assertNotIn(b'Private company',result.body)
