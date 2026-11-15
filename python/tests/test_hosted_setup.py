import io,json,unittest
from contextlib import contextmanager
from artex.server.hosted_setup import build_hosted_application
from artex.server.auth import sign_jwt
from artex.agent.engine_runner import EngineRunner
from artex.agent.engine import EngineScope
from artex.manager import RuntimeUnavailable
from test_tasks_store import ROW
from test_application import TestCodec
import test_engine

class DB(test_engine.DB):
    def __init__(self):
        super().__init__()
        self.calls=[];row=list(ROW);row[0]=5;row[6]=9;row[7]='running';row[8]=True;self.row=tuple(row)
    @contextmanager
    def transaction(self):yield self
    def fetch_one(self,q):self.calls.append(q);raise AssertionError(q.sql)
    def execute(self,q):self.calls.append(q);raise AssertionError(q.sql)
    def fetch_all(self,q):
        self.calls.append(q)
        if 'token_metrics.input_tokens' in q.sql:return [(5,9,*([0]*12),None)]
        if q.sql.startswith('UPDATE tasks SET status='):
            row=list(self.row);row[7]=q.params[1];row[8]=False;self.row=tuple(row);return [self.row]
        if q.sql.startswith('UPDATE tasks SET paused='):
            row=list(self.row);row[8]=True;self.row=tuple(row);return [self.row]
        if q.sql.startswith('SELECT id, COALESCE(name,'):return [self.row]
        if 'task_llm_profiles' in q.sql or 'FROM task_relations' in q.sql or 'FROM task_scope' in q.sql:return []
        return super().fetch_all(q)

class HostedSetupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_engine.EngineTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.db=DB()
        self.fixture.db=self.db;self.fixture.graph.executor=self.db;self.fixture.worker.store.executor=self.db
        self.factory_calls=[];self.capacity=[];self.bundle=None
        def factory(task,token,scope):
            self.factory_calls.append(task)
            self.assertTrue(self.bundle.host.poll().busy)
            self.assertEqual(self.capacity[-1][0],'enter')
            self.fixture.token=token;return self.fixture.make()
        self.runner=EngineRunner(readiness=lambda task:EngineScope(5,9,1,'r1'),factory=factory)
        @contextmanager
        def guard(operation,task_id):
            self.capacity.append(('enter',operation,task_id))
            try:yield True
            finally:self.capacity.append(('exit',operation,task_id))
        self.guard=guard
    def build(self,**kwargs):
        self.bundle=build_hosted_application(self.db,b'x'*32,TestCodec(),runner=kwargs.pop('runner',self.runner),
            admission_guard=kwargs.pop('admission_guard',self.guard),live_last_activity=lambda id:0,**kwargs)
        return self.bundle
    def request(self,path,data=None,authorized=True):
        raw=json.dumps(data or {}).encode();status=[]
        env={'REQUEST_METHOD':'POST','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
        if authorized:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(self.bundle.app(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]),json.loads(body)
    def test_load_inert_exact_ownership_and_http_resume_pause(self):
        b=self.build()
        self.assertIs(b.manager.runner,b.runner);self.assertIs(b.host.manager,b.manager);self.assertIs(b.facade.host,b.host)
        self.assertFalse(self.factory_calls);self.assertFalse(self.runner.handles())
        self.assertTrue(all(q.sql.startswith('SELECT') for q in self.db.calls))
        self.assertEqual(self.request('/api/tasks/5/control',{'action':'resume'},False)[0],401)
        self.assertEqual(self.request('/api/tasks/5/control',{'action':'resume'})[0],200)
        self.assertEqual(len(self.factory_calls),1);self.assertEqual(len(self.runner.handles()),1)
        self.assertFalse(self.fixture.pp.calls);self.assertFalse(self.fixture.wp.calls)
        self.assertEqual(self.request('/api/tasks/5/control',{'action':'pause'})[0],200)
        self.assertEqual([x[1] for x in self.capacity],['resume','resume'])
        self.assertTrue(b.host.drain(0).all_settled);self.assertFalse(self.runner.handles())
    def test_foreign_graph_executor_rejected_without_child_registration(self):
        self.build()
        foreign=test_engine.DB()
        self.fixture.graph.executor=foreign;self.fixture.worker.store.executor=foreign
        self.assertEqual(self.request('/api/tasks/5/control',{'action':'resume'})[0],503)
        self.assertFalse(self.runner.handles())
        self.assertFalse(self.fixture.pp.calls);self.assertFalse(self.fixture.wp.calls)
        self.assertTrue(self.db.row[8])  # manager compensates admission to paused

    def test_create_unsupported_no_task_write(self):
        self.build();before=len(self.db.calls)
        status,_=self.request('/api/tasks',{'description':'fixture','goal':'fixture'})
        self.assertEqual(status,503)
        self.assertFalse(any(not q.sql.startswith('SELECT') for q in self.db.calls[before:]))
        self.assertFalse(self.factory_calls)
    def test_bad_configuration_fails_before_reads(self):
        for options in ({'task_manager':object()},{'category_manager':object()},{'executor':object()},
                        {'unexpected':True},{'max_body_bytes':True}):
            with self.assertRaises(ValueError):self.build(application_options=options)
        with self.assertRaises(ValueError):self.build(admission_guard=None)
        with self.assertRaises(RuntimeUnavailable):self.build(runner=object())
        self.assertFalse(self.db.calls)
    def test_failed_load_starts_no_work(self):
        def fail(q):raise RuntimeError('fixture database unavailable')
        self.db.fetch_all=fail
        with self.assertRaises(RuntimeError):self.build()
        self.assertFalse(self.runner.handles());self.assertFalse(self.factory_calls)
        with self.assertRaises(RuntimeUnavailable):self.build()
    def test_empty_runner_cannot_be_claimed_by_two_hosts_or_factories(self):
        from artex.agent.host import AgentHost
        first=self.build();self.runner.claim_host(first.host)
        before=len(self.db.calls)
        with self.assertRaises(RuntimeUnavailable):self.build()
        with self.assertRaises(RuntimeUnavailable):AgentHost(first.manager,self.runner)
        self.assertEqual(len(self.db.calls),before)
        self.assertFalse(self.runner.handles())

    def test_existing_runner_work_not_adopted(self):
        self.runner._handles['5']=self.fixture.handle
        with self.assertRaises(RuntimeUnavailable):self.build()
        self.assertFalse(self.db.calls)
        self.assertIs(self.runner.handles()[0],self.fixture.handle)
        self.runner._handles.clear()

if __name__=='__main__':unittest.main()
