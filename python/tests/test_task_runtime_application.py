"""Actual composed runtime/WSGI, synthetic stored roles and SQL, no network."""
import io,json,unittest
from contextlib import nullcontext
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.agent.task_providers import TaskProviderResolver
from artex.agent.runtime import Grant
from artex.server.task_runtime_setup import build_task_runtime_application
from artex.server.auth import sign_jwt
from test_application import TestCodec
import test_task_providers as providers
import test_bootstrap_handle as bootstrap

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=bootstrap.BootstrapHandleTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.p=providers.Tests();self.p.setUp()
        for store in (self.p.tasks,self.p.agents,self.p.profiles):store.executor=self.f.db
        self.resolver=TaskProviderResolver(self.p.tasks,self.p.agents,self.p.profiles,self.p.factory,
            current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),authorize_task=lambda *a:True,
            authorize_profile=lambda *a:True,allow_global=False)
        self.runtime=TaskRuntimeAssembly(self.f.db,providers=self.resolver,transcript=self.f.f.planner.transcript,
            prompts={'goals':'Explicit goals','planner':'Explicit planner','worker':'Explicit worker'},policy_revision='r1',
            current=lambda *a:True,lifecycle_guard=self.f.f.guard,authorize=lambda *a:Grant(True,True,True),
            asset_authorize=lambda *a:True,validate_input=lambda *a:True,graph_catalog={'planner':(),'worker':()})
    def build(self,**kw):
        args=dict(live_last_activity=lambda id:0,profile_available=lambda id:True,single_process=True,pump_clock=lambda:0)
        args.update(kw)
        self.bundle=build_task_runtime_application(self.runtime,b'x'*32,TestCodec(),**args)
        self.addCleanup(lambda:self.bundle.stop_pump(1))
        return self.bundle
    def request(self,path,data,auth=True):
        raw=json.dumps(data).encode();status=[]
        env={'REQUEST_METHOD':'POST','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(self.bundle.app(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]),json.loads(body)
    def test_one_inert_manager_host_admission_pump(self):
        b=self.build()
        self.assertIs(b.admission.host,b.host);self.assertIs(b.facade.host,b.host)
        self.assertIs(b.pump.manager,b.manager);self.assertIsNone(b.pump._thread)
        self.assertEqual(self.p.calls,[]);self.assertEqual(self.p.construct,[])
        self.assertEqual(b.runner.handles(),())
    def test_jwt_blocks_mutation_before_provider_or_journal(self):
        self.build();before=list(self.f.db.calls)
        self.assertEqual(self.request('/api/tasks',{'description':'inert','goal':'inert'},False)[0],401)
        self.assertEqual(self.f.db.calls,before);self.assertIsNone(self.f.db.receipt)
        self.assertEqual(self.p.calls,[]);self.assertEqual(self.p.construct,[])
    def test_invalid_pump_configuration_before_startup_reads(self):
        before=list(self.f.db.calls)
        with self.assertRaises(ValueError):self.build(pump_interval=0)
        self.assertEqual(self.f.db.calls,before);self.assertEqual(self.p.calls,[])
    def test_admission_does_not_call_transport_without_explicit_tick(self):
        b=self.build();b.facade.start('5')
        self.assertEqual(self.p.calls,[]);self.assertEqual(self.p.construct,[])
        self.assertIsNone(self.f.db.receipt);self.assertEqual(len(b.runner.handles()),1)
    def test_jwt_create_then_explicit_tick_uses_actual_goals_transport(self):
        from artex.providers import Response
        from test_planner_cycle import node
        db=self.f.db;original_one=db.fetch_one;original_execute=db.execute
        def one(q):
            if q.sql.startswith('INSERT INTO explorations'):return (9,)
            if q.sql.startswith('INSERT INTO tasks('):
                row=list(db.row);row[2]=q.params[0];row[4]=q.params[2];row[5]=q.params[3];row[7]='created';row[8]=False;db.row=tuple(row);return db.row
            return original_one(q)
        def execute(q):
            if q.sql.startswith('INSERT INTO exploration_nodes'):
                db.rows=[node(1,'fact',json.loads(q.params[1]),'origin')];return 1
            return original_execute(q)
        db.fetch_one=one;db.execute=execute
        messages=[{'role':'assistant','content':None,'tool_calls':[{'id':'g1','type':'function','function':{'name':'set_goals','arguments':json.dumps({'goals':[{'text':'explicit outcome','vulnclass':''}]})}}]},
                  {'role':'assistant','content':'goals submitted'}]
        def transport(config):
            self.p.construct.append(config.model)
            def invoke(*a,**kw):
                self.p.calls.append(1);message=messages.pop(0)
                return Response(200,json.dumps({'choices':[{'message':message,'finish_reason':'tool_calls' if 'tool_calls' in message else 'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}}))
            return invoke
        self.p.factory._transport_factory=transport
        b=self.build()
        status,result=self.request('/api/tasks',{'description':'authorized synthetic fixture','goal':'explicit outcome','llm_profile_ids':[2]})
        self.assertEqual(status,201,result)
        self.assertEqual(self.p.calls,[]);self.assertIsNone(db.receipt)
        tick=b.tick()
        self.assertEqual(tick.advanced,('5',));self.assertEqual(self.p.calls,[1,1])
        self.assertEqual(db.receipt[7],'prepared')
        self.assertEqual(b.runner.handles()[0].phase,'engine')
        self.assertEqual(b.manager.status('5'),'running')
