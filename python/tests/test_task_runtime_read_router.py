import unittest,io,json
from contextlib import nullcontext
from types import SimpleNamespace
from artex.db.tasks import TaskStore
from artex.server.task_read_builder import TaskReadBuilder
from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
from artex.server.application import build_application
from artex.server.auth import sign_jwt
from test_application import TestCodec

class DB:
    def __init__(self):self.scopes={5:9,6:10};self.calls=[]
    def fetch_all(self,q):
        self.calls.append(q)
        if q.sql.startswith('SELECT exploration_id FROM tasks'):
            return [(q.params[1],)] if self.scopes.get(q.params[0])==q.params[1] else []
        if q.sql.startswith('SELECT id FROM tasks WHERE exploration_id='):return [(k,) for k,v in self.scopes.items() if v==q.params[0]]
        if 'exploration_nodes' in q.sql:return []
        if q.sql.startswith('SELECT id,name,format,'):return []
        raise AssertionError(q.sql)
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.tasks=TaskStore(self.db);self.fetches=[];self.denied=set();self.grants=[]
        def get(id):
            self.fetches.append(id)
            return SimpleNamespace(id=id,exploration_id=self.db.scopes[id]) if id in self.db.scopes else None
        self.tasks.get=get
        self.builder=TaskReadBuilder(self.db,request_authorize=lambda req,s,op:True,context_authorize=lambda *a:True,
            asset_authorize=lambda *a:True,runtime_snapshot=lambda *a:None,activity_authorize=lambda *a:True,
            activity_raw_authorize=lambda *a:True,activity_request_authorize=lambda *a:True,stream_authorize=lambda *a:True)
        self.router=TaskRuntimeReadRouter(self.tasks,self.builder,revision='read-policy-1',
            authorize_selector=lambda req,id:self.grants.append(id) or id not in self.denied,
            current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),authorize_profiles=lambda req:True)
        self.app=self.router.bind(build_application(self.db,b'x'*32,TestCodec()))
    def request(self,path,query='',auth=True):
        status=[];env={'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':query,'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(self.app(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]),json.loads(body)
    def test_jwt_then_selector_before_any_task_lookup(self):
        self.assertEqual(self.request('/api/exploration/intents','task=5',False)[0],401)
        self.assertEqual(self.fetches,[]);self.assertEqual(self.grants,[])
        self.denied.add(5)
        self.assertEqual(self.request('/api/exploration/intents','task=5')[0],403)
        self.assertEqual(self.fetches,[]);self.assertEqual(self.db.calls,[])
    def test_two_tasks_and_new_task_without_rebinding(self):
        for id in (5,6):self.assertEqual(self.request('/api/exploration/intents','task='+str(id))[0],200)
        self.db.scopes[7]=11
        self.assertEqual(self.request('/api/exploration/intents','task=7')[0],200)
        self.assertEqual(self.fetches,[5,6,7])
    def test_explicit_selector_no_fallback(self):
        for q in ('','task=active','task=05','task=5&task=6'):
            self.assertEqual(self.request('/api/exploration/intents',q)[0],400)
        self.assertEqual(self.fetches,[])
    def test_profiles_require_separate_grant_and_no_taskfetch(self):
        self.assertEqual(self.request('/api/llm/profiles')[0],200)
        self.assertEqual(self.fetches,[]);self.assertEqual(self.grants,[])
    def test_exp_changed_after_resolution_refused(self):
        old=self.tasks.get
        def changing(id):
            value=old(id);self.db.scopes[id]=44;return value
        self.tasks.get=changing
        self.assertEqual(self.request('/api/exploration/intents','task=5')[0],404)
    def test_executor_rebinding_denied_before_taskfetch(self):
        self.tasks.executor=object()
        self.assertEqual(self.request('/api/exploration/intents','task=5')[0],403)
        self.assertEqual(self.fetches,[])
