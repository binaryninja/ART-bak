"""Explicit assembled HTTP/capacity/bootstrap path with real authored adapters."""
import io,json,unittest
from artex.agent.bootstrap_setup import *
from artex.agent.pump import EnginePump
from artex.server.auth import sign_jwt
from artex.manager import RuntimeUnavailable
from test_application import TestCodec
import test_bootstrap_handle as fixtures
from test_planner_cycle import node

class BootstrapSetupTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BootstrapHandleTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.selections=[];self.bundle=None
        self.configuration=BootstrapConfiguration(self.f.f.ps.model,'explicit goals only','r1','request')
        def select(task):self.selections.append(task);return self.configuration
        self.select=select
    def build(self,**kw):
        args=dict(coordinator=self.f.coordinator,bootstrap_configuration=self.select,
            engine_factory=self.f.child_factory,initialized_readiness=lambda t:self.fail('unexpected initialized readiness'),
            initialized_factory=lambda *a:self.fail('unexpected initialized factory'),live_last_activity=lambda id:0,
            capacity=1,single_process=True)
        args.update(kw)
        self.bundle=build_bootstrap_application(self.f.db,b'x'*32,TestCodec(),**args)
        return self.bundle
    def request(self,path,data=None,authorized=True):
        raw=json.dumps(data or {}).encode();status=[]
        env={'REQUEST_METHOD':'POST','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
        if authorized:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(self.bundle.app(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]),json.loads(body)
    def test_constructor_loads_only_no_selection_journal_or_work(self):
        b=self.build();self.assertEqual(self.selections,[]);self.assertIsNone(self.f.db.receipt)
        self.assertEqual(self.f.provider.calls,[]);self.assertEqual(b.runner.handles(),())
        self.assertIs(b.admission.host,b.host);self.assertIs(b.facade.host,b.host)
    def test_same_capacity_gate_and_actual_first_tick(self):
        b=self.build();b.facade.start('5');h=b.runner.handles()[0]
        with self.assertRaises(RuntimeUnavailable):b.facade.start('5')
        self.assertIsNone(self.f.db.receipt)
        pump=EnginePump(b.host,clock=lambda:0);self.addCleanup(lambda:pump.stop(1))
        self.assertEqual(pump.tick().advanced,('5',));self.assertEqual(h.phase,'engine')
        self.assertEqual(self.f.f.pp.calls,[]);self.assertEqual(b.manager.status('5'),'running')
        self.assertEqual(self.request('/api/tasks/5/control',{'action':'pause'},False)[0],401)
        self.assertEqual(self.request('/api/tasks/5/control',{'action':'pause'})[0],200)
        self.assertEqual(b.admission.poll().pending,0)
    def test_invalid_capacity_fails_before_startup_reads(self):
        before=len(self.f.db.calls)
        with self.assertRaises(ValueError):self.build(capacity=0)
        self.assertEqual(len(self.f.db.calls),before);self.assertEqual(self.selections,[])
    def test_foreign_coordinator_executor_fails_before_startup_reads(self):
        before=len(self.f.db.calls);self.f.coordinator.journal.executor=object()
        with self.assertRaises(ValueError):self.build()
        self.assertEqual(len(self.f.db.calls),before)
    def test_bad_configuration_rejects_before_admission_write(self):
        b=self.build(bootstrap_configuration=lambda t:True)
        with self.assertRaises(RuntimeUnavailable):b.facade.start('5')
        self.assertEqual(b.manager.status('5'),'created');self.assertEqual(b.runner.handles(),())
    def test_configuration_callback_resource_rebind_fails_closed(self):
        def select(task):self.f.coordinator.journal.executor=object();return self.configuration
        b=self.build(bootstrap_configuration=select)
        with self.assertRaises(RuntimeUnavailable):b.facade.start('5')
        self.assertEqual(b.runner.handles(),());self.assertIsNone(self.f.db.receipt)
    def test_actual_http_create_installs_before_journal_and_model(self):
        db=self.f.db;original_one=db.fetch_one;original_execute=db.execute
        def fetch_one(q):
            if q.sql.startswith('INSERT INTO explorations'):return (9,)
            if q.sql.startswith('INSERT INTO tasks('):
                row=list(db.row);row[2]=q.params[0];row[4]=q.params[2];row[5]=q.params[3];row[7]='created';row[8]=False;db.row=tuple(row);return db.row
            return original_one(q)
        def execute(q):
            if q.sql.startswith('INSERT INTO exploration_nodes'):
                db.rows=[node(1,'fact',json.loads(q.params[1]),'origin')];return 1
            return original_execute(q)
        db.fetch_one=fetch_one;db.execute=execute
        b=self.build()
        status,result=self.request('/api/tasks',{'description':'authorized synthetic fixture','goal':'explicit outcome'})
        self.assertEqual(status,201,result);self.assertEqual(result['status'],'running')
        self.assertEqual(len(b.runner.handles()),1);self.assertIsNone(db.receipt);self.assertEqual(self.f.provider.calls,[])
        h=b.runner.handles()[0];b.host.advance_owned(lambda:b.runner.advance(h.task_id,h.token,0))
        self.assertEqual(h.phase,'engine');self.assertEqual(db.receipt[7],'prepared')
