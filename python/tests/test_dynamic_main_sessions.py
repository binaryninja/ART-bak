"""Frontend ordered boot requests against actual components and synthetic SQL."""
import io,json,unittest
from contextlib import nullcontext
from datetime import datetime,timezone
from types import SimpleNamespace
from artex.db.tasks import TaskStore
from artex.server.task_read_builder import TaskReadBuilder
from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
from artex.server.application import build_application
from artex.server.auth import sign_jwt
from test_application import TestCodec
from test_task_activity_read import DB as ActivityDB

class DB(ActivityDB):
    def __init__(self):super().__init__();self.scopes={5:9,6:10};self.session_after=lambda:None
    def fetch_all(self,q):
        if q.sql.startswith('SELECT exploration_id FROM tasks'):
            self.queries.append(q);return [(self.scopes[q.params[0]],)] if q.params[0] in self.scopes and (len(q.params)==1 or self.scopes[q.params[0]]==q.params[1]) else []
        if q.sql.startswith('SELECT id FROM tasks WHERE id='):
            self.queries.append(q);return [(q.params[0],)] if self.scopes.get(q.params[0])==q.params[1] else []
        if q.sql.startswith('SELECT seq,created_at FROM main_sessions'):
            self.queries.append(q);self.session_after();return [(q.params[0],datetime(2026,1,1,tzinfo=timezone.utc))]
        return super().fetch_all(q)

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.selector=True;self.source=True;self.request_grant=True;self.lookups=[]
        self.build()
    def build(self,enabled=True):
        tasks=TaskStore(self.db)
        def get(id):
            self.lookups.append(id);return SimpleNamespace(id=id,exploration_id=self.db.scopes[id])
        tasks.get=get
        options=dict(main_sessions_authorize=lambda s:self.source,main_sessions_request_authorize=lambda req,s:self.request_grant) if enabled else {}
        self.builder=TaskReadBuilder(self.db,request_authorize=lambda *a:True,context_authorize=lambda *a:True,
            asset_authorize=lambda *a:True,runtime_snapshot=lambda *a:None,activity_authorize=lambda *a:True,
            activity_raw_authorize=lambda *a:True,activity_request_authorize=lambda *a:True,stream_authorize=lambda *a:True,**options)
        self.router=TaskRuntimeReadRouter(tasks,self.builder,revision='r1',authorize_selector=lambda *a:self.selector,
            current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
        self.app=self.router.bind(build_application(self.db,b'x'*32,TestCodec()))
    def request(self,path,query,auth=True):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':query,'wsgi.input':io.BytesIO()}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(self.app(env,lambda s,h:statuses.append(s)))
        return int(statuses[0].split()[0]),body
    def test_two_task_frontend_boot_order_same_app(self):
        app=self.app
        for task,exp in ((5,9),(6,10)):
            status,body=self.request('/api/exploration/main-sessions',f'task={task}')
            self.assertEqual(status,200);value=json.loads(body);self.assertEqual(value['current'],exp)
            status,body=self.request('/api/exploration/activity/history',f'task={task}&session=main:{exp}&before=0&limit=2')
            self.assertEqual(status,200);cursor=json.loads(body)['snapshot_cursor']
            self.assertEqual(self.request('/api/exploration/activity/stream',f'task={task}&since={cursor}')[0],200)
            self.assertIs(self.app,app)
        self.assertEqual([q.params[0] for q in self.db.queries if q.sql.startswith('SELECT seq,')],[9,10])
    def test_jwt_selector_before_lookup(self):
        self.assertEqual(self.request('/api/exploration/main-sessions','task=5',False)[0],401)
        self.selector=False;self.assertEqual(self.request('/api/exploration/main-sessions','task=5')[0],403)
        self.assertEqual(self.lookups,[]);self.assertEqual(self.db.queries,[])
    def test_separate_grants_and_late_revocation(self):
        self.source=False;self.assertEqual(self.request('/api/exploration/main-sessions','task=5')[0],403)
        self.source=True;self.request_grant=False;self.assertEqual(self.request('/api/exploration/main-sessions','task=5')[0],403)
        self.request_grant=True;self.db.session_after=lambda:setattr(self,'request_grant',False)
        status,body=self.request('/api/exploration/main-sessions','task=5');self.assertEqual(status,403);self.assertNotIn(b'2026',body)
    def test_optional_missing_and_original_policy_rebind(self):
        self.build(False);self.assertEqual(self.request('/api/exploration/main-sessions','task=5')[0],503)
        self.builder._session_policies=(lambda s:True,lambda r,s:True)
        before=len(self.lookups);self.assertNotEqual(self.request('/api/exploration/main-sessions','task=5')[0],200)
        self.assertEqual(len(self.lookups),before)
