"""Real authored planner/worker/store bridge with inert SQL/providers/temp JSONL."""
import unittest,tempfile,json
from contextlib import contextmanager
from threading import RLock,Thread,Event
from copy import deepcopy
from artex.agent.engine import *
from artex.agent.chat import ChatBinding
from artex.agent.runtime import Grant
from artex.db.workers import WorkerStore
from artex.tools.registry import ToolRegistry
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
import test_planner_cycle as fixture
from test_chat_runtime import Provider,MODEL,text

class DB(fixture.GraphDB):
    def __init__(self):super().__init__();self.versions={};self.fail_settle=False
    def fetch_all(self,q):
        if q.sql.startswith('SELECT src_id, rel, dst_id FROM exploration_edges'):
            self.queries.append(q);return [(p[1],p[2],p[3]) for p in self.links if len(p)==4 and isinstance(p[2],str)]
        if 'SELECT id FROM tasks' in q.sql:
            self.queries.append(q);return [(5,)] if self.live else []
        if 'content_version FROM exploration_nodes' in q.sql:
            self.queries.append(q)
            return [(*r,self.versions.get(r[0],0)) for r in self.rows if r[0]==q.params[1] and r[1]=='intent']
        if "state='open' ORDER BY priority DESC" in q.sql:
            self.queries.append(q);return sorted([r for r in self.rows if r[1]=='intent' and r[4]=='open'],key=lambda r:(-r[3],r[0]))[:q.params[-1]]
        return super().fetch_all(q)
    def execute(self,q):
        if "SET state='running', owner=" in q.sql:
            self.queries.append(q);id=q.params[1]
            for i,r in enumerate(self.rows):
                if r[0]==id and r[4]=='open' and self.versions.get(id,0)==q.params[-1]:
                    row=list(r);row[4]='running';row[6]=q.params[0];self.rows[i]=tuple(row);self.versions[id]=q.params[-1]+1;return 1
            return 0
        if q.sql.startswith('UPDATE exploration_nodes SET state=$1'):
            if self.fail_settle:raise RuntimeError('fixture persistence failure')
            self.queries.append(q);id=q.params[1]
            for i,r in enumerate(self.rows):
                if r[0]==id and r[4]==q.params[3] and self.versions.get(id,0)==q.params[4]:
                    row=list(r);row[4]=q.params[0];self.rows[i]=tuple(row);self.versions[id]+=1;return 1
            return 0
        return super().execute(q)

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=DB();self.graph=ExplorationStore(self.db,9);self.lock=RLock();self.valid=True
        @contextmanager
        def guard(*args):
            with self.lock:yield
        self.guard=guard;self.token=CancellationToken()
        transcript=ChatTranscriptAdapter(TranscriptStore(self.tmp.name))
        self.pp=Provider([fixture.intent(),text('queued'),text('needs review')]);self.wp=Provider([text('worker completed')])
        self.ps=PlannerScope(5,9,1,'r1',MODEL,'inert planner')
        self.planner=PlannerCycle(store=self.graph,transcript=transcript,current=lambda s:self.valid and not self.token.cancelled,lifecycle_guard=guard,
            provider_resolver=lambda s,m:ChatBinding(m,self.pp),authorize=lambda *a:Grant(True,True,True),asset_authorize=lambda *a:Grant(True,True,True))
        def registry(scope,record):
            r=ToolRegistry('worker',handlers=[],current=lambda rev:self.valid,lifecycle_guard=lambda rev:guard(),authorize=lambda *a:Grant(True,True,True));r.reload([],scope.revision);return r
        self.worker=IntentWorker(store=WorkerStore(self.db,5,9,lifetime_guard=guard),transcript=transcript,
            current=lambda s,phase:self.valid and (phase=='settle' or not self.token.cancelled),lifecycle_guard=guard,
            provider_resolver=lambda s,m:ChatBinding(m,self.wp),registry_factory=registry,authorize=lambda *a:Grant(True,True,True))
        self.handle=self.make()
    def make(self,**kw):
        return TaskEngineHandle(EngineScope(5,9,1,'r1'),self.token,store=self.graph,planner=self.planner,planner_scope=self.ps,
            worker=self.worker,worker_model=MODEL,worker_system='inert worker',owner='work#1',current=lambda s,t:self.valid and t is self.token,
            lifecycle_guard=self.guard,single_process=True,**kw)
    def seed(self,id=3,priority=5,state='open'):
        r=list(fixture.node(id,'intent',{'summary':'inert seed'},state));r[3]=priority;self.db.rows.append(tuple(r))
    def met(self):
        r=list(self.db.rows[1]);r[4]='met';self.db.rows[1]=tuple(r)
    def test_actual_planner_worker_next_planner_path(self):
        self.assertEqual(self.handle.advance(0).action,'planner')
        self.assertEqual(self.db.rows[-1][1:2],('intent',))
        self.assertEqual(self.handle.advance(1).action,'worker')
        self.assertEqual(self.db.rows[-1][4],'done');self.assertEqual(self.db.versions[3],2)
        self.assertEqual(self.handle.advance(2).action,'planner')
        self.assertIn('done',self.pp.calls[-1]['messages'][0]['content'][0]['text'])
        settled=[];self.assertFalse(self.handle.settle_completed(lambda *a:settled.append(a) or True));self.assertEqual(settled,[])
        self.assertTrue(self.handle.wait(0))
    def test_seed_suppresses_first_planner_priority_order(self):
        self.seed(3,2);self.seed(4,9)
        result=self.handle.advance(0)
        self.assertEqual(result.intent_id,4);self.assertEqual(self.pp.calls,[])
        self.assertEqual(self.db.rows[2][4],'open')
    def test_completion_requeries_not_cached_result(self):
        self.met();self.assertTrue(self.handle.advance(0).completion_ready)
        self.seed();receipts=[]
        self.assertFalse(self.handle.settle_completed(lambda *a:receipts.append(a) or True));self.assertEqual(receipts,[])
        self.handle.advance(1)
        self.assertTrue(self.handle.settle_completed(lambda *a:receipts.append(a) or True))
        self.assertIs(receipts[0][1],self.token);self.assertFalse(self.token.cancelled)
        self.assertEqual(self.handle.advance(2).action,'closed')
    def test_unowned_running_quarantines_without_model(self):
        self.seed(state='running')
        with self.assertRaises(EngineUnresolved):self.handle.advance(0)
        self.assertTrue(self.handle.unresolved);self.assertEqual(self.pp.calls,[]);self.assertEqual(self.wp.calls,[])
    def test_worker_persistence_uncertainty_stops_next_work(self):
        self.seed();self.db.fail_settle=True
        with self.assertRaises(EngineUnresolved):self.handle.advance(0)
        with self.assertRaises(EngineUnresolved):self.handle.advance(1)
        self.assertEqual(len(self.wp.calls),1);self.assertFalse(self.handle.settle_completed(lambda *a:True))
    def test_notification_dedup_and_bounded_receipts(self):
        self.handle=self.make(max_events=1)
        self.assertTrue(self.handle.notify('one','hint'));self.assertFalse(self.handle.notify('one','hint'))
        with self.assertRaises(EngineUnavailable):self.handle.notify('two','hint')
    def test_cancel_busy_worker_drains_without_task_completion(self):
        self.seed();entered=Event();release=Event();results=[]
        self.wp.hook=lambda:(entered.set(),release.wait(2))
        def run():
            try:results.append(self.handle.advance(0))
            except BaseException as e:results.append(e)
        thread=Thread(target=run);thread.start();self.assertTrue(entered.wait(1))
        self.assertEqual(self.handle.advance(1).action,'busy');self.handle.cancel();self.assertFalse(self.handle.wait(0))
        release.set();thread.join(2);self.assertFalse(thread.is_alive());self.assertTrue(self.handle.wait(0))
        self.assertEqual(self.db.rows[-1][4],'open');self.assertFalse(self.handle.settle_completed(lambda *a:True))
    def test_paused_intents_not_false_completion_and_missing_bootstrap_rejected(self):
        self.met();self.seed(state='paused')
        self.assertFalse(self.handle.advance(0).completion_ready)
        self.assertFalse(self.handle.settle_completed(lambda *a:True))
        self.db.rows=[self.db.rows[0]]
        with self.assertRaises(EngineUnavailable):self.handle.advance(1)

    def test_connected_worker_records_fact_through_actual_graph_tool(self):
        from artex.tools.graph import GraphTools,GraphScope
        from artex.db.tool_catalog import CatalogTool
        def registry(scope,record):
            native=GraphTools(self.graph,GraphScope(5,9,1,'r1','work#1',record.id),
                current=lambda s:self.valid and not self.token.cancelled,lifecycle_guard=self.guard,
                authorize=lambda *a:Grant(True,True,True),asset_authorize=lambda *a:Grant(True,True,True))
            tools=[t for t in native.tools() if t.name=='record_fact']
            result=ToolRegistry('worker',handlers=tools,current=lambda r:self.valid,lifecycle_guard=lambda r:self.guard(),authorize=lambda *a:Grant(True,True,True))
            result.reload([CatalogTool(t.name,True,t.description,t.input_schema,['worker'],True,'builtin',{},False) for t in tools],'r1')
            return result
        self.worker.registry_factory=registry
        self.wp.responses=[({'role':'assistant','content':[{'type':'tool_use','id':'observed1','name':'record_fact','input':{'summary':'inert observation','confidence':'observed'}}]},'tool_use',{}),text('recorded')]
        self.handle.advance(0);self.handle.advance(1)
        facts=[r for r in self.db.rows if r[1]=='fact' and r[4]=='confirmed']
        self.assertEqual(len(facts),1);self.assertIn('inert observation',facts[0][2])
        self.assertIn((9,3,'yields',facts[0][0]),self.db.links)
        self.assertEqual(next(r for r in self.db.rows if r[0]==3)[4],'done')

    def test_actual_worker_native_context_tool_receives_exact_run_metadata(self):
        from artex.tools.context import GraphContextTools
        from artex.agent.graph_context import GraphContext,ContextScope
        def registry(scope,record):
            cs=ContextScope(scope.task_id,scope.exploration_id,scope.run_id,scope.revision)
            context=GraphContext(self.graph,cs,current=lambda s:self.valid and not self.token.cancelled,lifecycle_guard=self.guard)
            native=GraphContextTools(context,cs,gate=lambda *a:Grant(True,True,True),current=lambda s:self.valid)
            result=ToolRegistry('worker',handlers=native.tools(),current=lambda r:self.valid,lifecycle_guard=lambda r:self.guard(),authorize=lambda *a:Grant(True,True,True))
            result.reload(native.catalog(agents=['worker']),scope.revision)
            return result
        self.worker.registry_factory=registry
        self.seed()
        self.wp.responses=[({'role':'assistant','content':[{'type':'tool_use','id':'overview1','name':'graph_overview','input':{}}]},'tool_use',{}),text('read context')]
        self.assertEqual(self.handle.advance(0).action,'worker')
        returned=self.wp.calls[-1]['messages'][-1]['content'][0]
        self.assertFalse(returned.get('is_error',False))
        overview=json.loads(returned['content'][0]['text'])
        self.assertEqual(overview['scope'],dict(task_id=5,exploration_id=9,run_id=1,revision='r1'))
        self.assertEqual(self.db.rows[-1][4],'done')
