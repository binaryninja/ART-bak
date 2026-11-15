import json,tempfile,unittest
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from threading import RLock,Event,Thread
from artex.agent.worker import IntentWorker,WorkerScope,WorkerUnresolved
from artex.agent.chat import ChatBinding
from artex.agent.runtime import Grant,Limits,Tool
from artex.db.workers import WorkerStore,WorkerConflict
from artex.tools.registry import ToolRegistry
from artex.db.tool_catalog import CatalogTool
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
from test_workers import Executor,row
from test_chat_runtime import Provider,MODEL,text

class Database(Executor):
    def __init__(self):super().__init__();self.rows=[row('open',3)];self.fail_settle=False;self.fail_commit=False
    @contextmanager
    def transaction(self):
        old=deepcopy(self.rows)
        try:yield self
        except BaseException:self.rows=old;raise
    def fetch_one(self,q):
        self.queries.append(q)
        return (1,) if q.sql.startswith('SELECT id FROM tasks') else (7,)
    def execute(self,q):
        self.queries.append(q);old=list(self.rows[0])
        if "SET state='running', owner=" in q.sql:
            if old[4]!='open' or old[10]!=q.params[-1]:return 0
            old[4]='running';old[6]=q.params[0];old[10]+=1;self.rows=[tuple(old)];return 1
        if q.sql.startswith('UPDATE exploration_nodes SET state=$1'):
            if self.fail_settle:raise RuntimeError('secret db')
            if old[4]!=q.params[3] or old[10]!=q.params[4]:return 0
            old[4]=q.params[0];old[10]+=1;self.rows=[tuple(old)];return 1
        return 1

class WorkerCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.db=Database();self.lock=RLock()
        @contextmanager
        def guard(*args):
            with self.lock:yield
        self.guard=guard;self.store=WorkerStore(self.db,1,10,lifetime_guard=guard)
        self.transcript=ChatTranscriptAdapter(TranscriptStore(self.tmp.name));self.provider=Provider([text('done')]);self.valid=True;self.calls=[]
        self.scope=WorkerScope(1,10,1,'r1',7,3,'worker-fixture',MODEL,'Only execute the inert fixture intent.')
        self.worker=self.new_worker()
    def registry(self,scope,record):
        def callback(context,args):self.calls.append(args);return 'fixture observation'
        tool=Tool('Observe','inert',{'type':'object','properties':{},'additionalProperties':False},callback,lambda args:args=={})
        registry=ToolRegistry('worker',handlers=[tool],current=lambda revision:self.valid,lifecycle_guard=lambda rev:self.guard(),authorize=lambda *a:Grant(True,True,True))
        registry.reload([CatalogTool(tool.name,True,tool.description,tool.input_schema,['worker'],True,'builtin',{},False)],scope.revision)
        return registry
    def new_worker(self,**kw):
        return IntentWorker(store=self.store,transcript=self.transcript,current=lambda scope,phase:self.valid,lifecycle_guard=self.guard,
                            provider_resolver=lambda scope,model:ChatBinding(model,self.provider),registry_factory=self.registry,authorize=lambda *a:Grant(True,True,True),**kw)
    def test_actual_claim_transcript_and_state_settlement(self):
        result=self.worker.run(self.scope,'client')
        self.assertEqual(result.state,'done');self.assertEqual(result.version,5)
        self.assertEqual(self.db.rows[0][4],'done');self.assertEqual(self.db.rows[0][10],5)
        self.assertEqual(len(TranscriptStore(self.tmp.name).read('exp10-worker-i7')),2)
        self.assertEqual(result.outcome.result.text,'done') # does NOT prove any task goal
    def test_actual_registry_callback_consumed_by_worker(self):
        self.provider.responses=[({'role':'assistant','content':[{'type':'tool_use','id':'a','name':'Observe','input':{}}]},'tool_use',{}),text('observed')]
        result=self.worker.run(self.scope,'client');self.assertEqual(result.state,'done');self.assertEqual(self.calls,[{}])
    def test_restart_terminal_intent_never_reexecutes(self):
        self.worker.run(self.scope,'client');self.worker=self.new_worker()
        with self.assertRaises(WorkerConflict):self.worker.run(self.scope,'client')
        self.assertEqual(len(self.provider.calls),1)
    def test_sql_settlement_failure_requires_reconciliation(self):
        self.db.fail_settle=True
        with self.assertRaises(WorkerUnresolved):self.worker.run(self.scope,'client')
        self.assertEqual(self.db.rows[0][4],'running')
        self.worker=self.new_worker()
        with self.assertRaises(WorkerConflict):self.worker.run(self.scope,'client')
        self.assertEqual(len(self.provider.calls),1)
    def test_cancellation_causes_exact_states(self):
        for reason,state in [('worker_pause','paused'),('task_pause','open'),('planner_kill','stopped')]:
            with self.subTest(reason=reason):
                self.db.rows=[row('open',3)];self.provider=Provider([text('partial')]);self.transcript=ChatTranscriptAdapter(TranscriptStore(self.tmp.name));self.worker=self.new_worker()
                self.provider.hook=lambda:self.worker.cancel(7,reason)
                result=self.worker.run(self.scope,reason)
                self.assertEqual(result.state,state);self.assertEqual(result.outcome.result.reason,'cancelled')
    def test_budget_is_exhausted_not_done(self):
        self.worker=self.new_worker(limits=Limits(max_turns=1))
        self.provider.responses=[({'role':'assistant','content':[{'type':'tool_use','id':'a','name':'Observe','input':{}}]},'tool_use',{})]
        self.assertEqual(self.worker.run(self.scope,'client').state,'exhausted')
    def test_generation_loss_does_not_commit_or_settle(self):
        self.provider.hook=lambda:setattr(self,'valid',False)
        with self.assertRaises(WorkerUnresolved):self.worker.run(self.scope,'client')
        self.assertEqual(self.db.rows[0][4],'running')
    def test_invalid_client_and_foreign_scope_before_claim(self):
        with self.assertRaises(ValueError):self.worker.run(self.scope,'bad / key')
        with self.assertRaises(WorkerConflict):self.worker.run(replace(self.scope,exploration_id=99),'client')
        self.assertEqual(self.db.rows[0][4],'open');self.assertEqual(self.provider.calls,[])
    def test_parallel_workers_one_claim_one_execution(self):
        entered=Event();release=Event();failures=[];results=[]
        self.provider.hook=lambda:(entered.set(),release.wait(2))
        def first():
            try:results.append(self.worker.run(self.scope,'one'))
            except BaseException as exc:failures.append(exc)
        thread=Thread(target=first);thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(WorkerConflict):self.new_worker().run(self.scope,'two')
        finally:release.set();thread.join(2)
        self.assertEqual(failures,[]);self.assertEqual(len(results),1);self.assertEqual(len(self.provider.calls),1)

    def test_finalized_client_receipt_blocks_new_claim_generation(self):
        self.provider.hook=lambda:self.worker.cancel(7,'task_pause')
        self.worker.run(self.scope,'client')
        self.assertEqual(self.db.rows[0][4],'open');self.assertEqual(self.db.rows[0][10],5)
        self.worker=self.new_worker();next_scope=replace(self.scope,expected_version=5)
        from artex.transcripts import TranscriptConflict
        with self.assertRaises(TranscriptConflict):self.worker.run(next_scope,'client')
        self.assertEqual(self.db.rows[0][4],'open');self.assertEqual(self.db.rows[0][10],5)
        self.assertEqual(len(self.provider.calls),1)

    def test_cancel_after_final_decision_is_rejected(self):
        original=self.store.transition;accepted=[]
        def transition(*args,**kwargs):
            accepted.append(self.worker.cancel(7,'worker_pause'))
            return original(*args,**kwargs)
        self.store.transition=transition
        result=self.worker.run(self.scope,'client')
        self.assertEqual(accepted,[False]);self.assertEqual(result.state,'done')
