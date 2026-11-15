import json
import unittest
from contextlib import nullcontext
from test_task_scope import DB
from artex.db.task_scope import TaskScopeStore,normalize,ScopeInvalid
from artex.tools.task_scope import TaskScopeTools,NAME
from artex.agent.graph_context import ContextScope
from artex.agent.runtime import Grant,RunStopped

class Context(dict):
    cancelled=False
    def check(self):
        if self.cancelled:raise RunStopped('cancelled')

class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.approved=set();self.live=True;self.gates=[]
        self.store=TaskScopeStore(self.db,1,lifecycle_guard=nullcontext,current=lambda:self.live,approve=lambda e:e in self.approved)
        self.scope=ContextScope(1,2,3,'rev')
        self.context=Context(task_id=1,exploration_id=2,run_id=3,revision='rev')
        self.tools=TaskScopeTools(self.store,self.scope,gate=self.gate,current=lambda s:self.live,lifecycle_guard=lambda s:nullcontext())
    def gate(self,context,action):self.gates.append(action);return Grant(True,True,True)
    def approve(self,kind,value,reason=''):
        self.approved.add(normalize(1,kind,value,reason=reason))
    def call(self,args):return json.loads(self.tools.call(self.context,args))
    def test_single_source_shape_reason_and_kind_trim(self):
        self.approve('subdomain','a.example','evidence')
        result=self.call({'kind':' subdomain ','value':'A.Example.','reason':'evidence'})
        self.assertEqual(result,{'added':[{'kind':'subdomain','domain':'a.example','net':'','value':'','company_id':None}]})
        self.assertEqual(self.db.rows[0][-2:],('agent','evidence'))
    def test_batch_indexed_partial_success(self):
        self.approve('ip','127.0.0.1')
        result=self.call({'entries':[{'kind':'bad','value':'x'},{'kind':'ip','value':'127.0.0.1'},{'kind':'keyword','value':'unauthorized'}]})
        self.assertEqual(set(result['errors']),{'0','2'});self.assertEqual(len(result['added']),1);self.assertEqual(len(self.db.rows),1)
        self.assertEqual(len(self.gates),2)
    def test_empty_entries_falls_back_single(self):
        self.approve('keyword','x');self.assertEqual(len(self.call({'entries':[],'kind':'keyword','value':'x'})['added']),1)
    def test_gate_denied_never_writes(self):
        self.approve('keyword','x');self.tools.gate=lambda c,a:Grant(True,True,False)
        result=self.call({'kind':'keyword','value':'x'});self.assertIsNone(result['added']);self.assertEqual(self.db.rows,[])
    def test_scope_identity_and_paused_authority(self):
        for key,value in [('task_id',True),('exploration_id',8),('run_id',7),('revision','new')]:
            old=self.context[key];self.context[key]=value
            with self.assertRaises(RunStopped):self.call({'kind':'keyword','value':'x'})
            self.context[key]=old
        self.live=False
        with self.assertRaises(RunStopped):self.call({'kind':'keyword','value':'x'})
        self.assertEqual(self.db.rows,[])
    def test_cancel_during_gate(self):
        def gate(c,a):c.cancelled=True;return Grant(True,True,True)
        self.tools.gate=gate
        with self.assertRaises(RunStopped):self.call({'kind':'keyword','value':'x'})
        self.assertEqual(self.db.rows,[])
    def test_cancel_during_normalized_approval_rolls_back(self):
        def approve(entry):self.context.cancelled=True;return True
        self.store.approve=approve
        with self.assertRaises(RunStopped):self.call({'kind':'keyword','value':'x'})
        self.assertEqual(self.db.rows,[])
    def test_gate_cannot_mutate_arguments(self):
        args={'kind':'keyword','value':'safe'};self.approve('keyword','safe')
        def gate(c,a):args['value']='other';return Grant(True,True,True)
        self.tools.gate=gate;self.call(args);self.assertEqual(self.db.rows[0][5],'safe')
    def test_database_failure_not_hidden(self):
        self.db.fail=True;self.approve('keyword','one');self.approve('keyword','two')
        with self.assertRaises(RuntimeError):self.call({'entries':[{'kind':'keyword','value':'one'},{'kind':'keyword','value':'two'}]})
        self.assertEqual(len(self.db.rows),1) # Source partial commits, not blind retry.
    def test_no_inference_and_invalid_client_fields(self):
        self.approve('root_domain','example.com')
        self.assertIsNone(self.call({'kind':'root_domain','value':'api.example.com'})['added'])
        for args in [{'task_id':2},{'entries':'x'},{'entries':[{'kind':'ip','value':'x','reason':'x'}]},{'reason':4}]:
            with self.assertRaises(ScopeInvalid):self.call(args)
        self.assertEqual(self.db.rows,[])
    def test_explicit_catalog_and_tool(self):
        self.assertEqual(self.tools.tools()[0].name,NAME)
        self.assertEqual(len(self.tools.catalog(agents=['planner'])),1)
        with self.assertRaises(ValueError):self.tools.catalog(agents=[])

if __name__=='__main__':unittest.main()
