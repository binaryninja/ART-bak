from contextlib import nullcontext
from dataclasses import asdict,replace
import json
import unittest
from artex.agent.graph_context import GraphContext,ContextScope
from artex.db.exploration import ExplorationStore
from artex.agent.runtime import Grant,RunStopped,BoundedAgent
from artex.tools.context import GraphContextTools,ContextToolError
from test_conversation_activity import TransactionFake
from test_graph_tools import node
from test_agent_runtime import FakeProvider,assistant,request

class Context(dict):
    cancelled=False
    def check(self):
        if self.cancelled:raise RunStopped('cancelled')

class ContextToolTests(unittest.TestCase):
    def setUp(self):
        self.scope=ContextScope(3,9,1,'revision')
        self.db=TransactionFake([[(3,)],[node(1,'fact'),node(4,'digest',{'body':'persisted body'},'active')],[(4,'covers',1)]])
        self.live=True;self.grants=[]
        self.graph=GraphContext(ExplorationStore(self.db,9),self.scope,current=lambda s:self.live,lifecycle_guard=lambda s:nullcontext())
        self.context=Context(asdict(self.scope))
    def adapter(self,**kw):
        def gate(c,a):self.grants.append(a);return Grant(True,True,True)
        return GraphContextTools(self.graph,self.scope,gate=kw.pop('gate',gate),current=lambda s:self.live,**kw)
    def test_actual_local_snapshot_reads_only(self):
        output=json.loads(self.adapter().call('graph_overview',self.context,{}))
        self.assertEqual(output['scope'],asdict(self.scope));self.assertIn('inherited_sources',output['unavailable'])
        self.assertTrue(all(q.sql.startswith('SELECT') for _,q in self.db.calls))
        self.assertEqual(self.grants[0].arguments(),{})
    def test_actual_digest_schema_and_result(self):
        output=json.loads(self.adapter().call('expand_digest',self.context,{'id':4}))
        self.assertEqual(output['id'],4);self.assertEqual(output['members'][0]['id'],1)
        self.assertEqual(output['state'],'active');self.assertEqual(output['body'],'persisted body')
        self.assertEqual(self.grants[0].name,'expand_digest')
    def test_each_grant_dimension_fails_closed(self):
        for grant in (None,True,Grant(True,False,True),Grant(1,True,True)):
            with self.assertRaises(RunStopped):self.adapter(gate=lambda c,a:grant).call('graph_overview',self.context,{})
        self.assertFalse(self.db.calls)
    def test_missing_scope_or_cross_task_never_reads(self):
        for context in (Context(),Context({**asdict(self.scope),'task_id':4}),Context({**asdict(self.scope),'run_id':True})):
            with self.assertRaises(RunStopped):self.adapter().call('graph_overview',context,{})
        self.assertFalse(self.db.calls);self.assertFalse(self.grants)
    def test_cancel_and_scope_change_while_awaiting_gate(self):
        def cancel(c,a):c.cancelled=True;return Grant(True,True,True)
        with self.assertRaises(RunStopped):self.adapter(gate=cancel).call('graph_overview',self.context,{})
        self.context.cancelled=False
        def stale(c,a):self.live=False;return Grant(True,True,True)
        with self.assertRaises(RunStopped):self.adapter(gate=stale).call('graph_overview',self.context,{})
        self.assertFalse(self.db.calls)
    def test_arguments_snapshot_not_changed_by_gate(self):
        args={'id':4}
        def mutate(c,a):args['id']=999;a.arguments()['id']=999;return Grant(True,True,True)
        output=json.loads(self.adapter(gate=mutate).call('expand_digest',self.context,args))
        self.assertEqual(output['id'],4)
    def test_validation_rejects_unknown_fields_and_nonlocal_selectors(self):
        for name,args in (('graph_overview',{'task_id':4}),('expand_digest',{'id':True}),('expand_digest',{'id':0}),
                          ('expand_digest',{'id':2**63}),('expand_digest',{'id':4,'source_task_id':9}),('write',{})):
            with self.assertRaises(ValueError):self.adapter().call(name,self.context,args)
        self.assertFalse(self.db.calls)
    def test_stale_result_not_returned(self):
        original=self.graph.snapshot
        def read():
            result=original();self.live=False;return result
        self.graph.snapshot=read
        with self.assertRaises(RunStopped):self.adapter().call('graph_overview',self.context,{})
    def test_output_limit_fails_without_truncation(self):
        with self.assertRaises(ContextToolError):self.adapter(max_output_bytes=1).call('graph_overview',self.context,{})
    def test_metadata_is_copy_and_no_default_registration(self):
        adapter=self.adapter();tools=adapter.tools();rows=adapter.catalog(agents=('planner',))
        self.assertEqual({x.name for x in tools},{x.key for x in rows})
        self.assertEqual({x.key for x in rows},{'graph_overview','expand_digest'})
        rows[0].schema['properties']['bad']={}
        self.assertEqual(adapter.tools()[0].input_schema['properties'],{})
        self.assertFalse(self.db.calls)
        with self.assertRaises(ValueError):adapter.catalog(agents=())
    def test_bounded_agent_real_registered_read_and_pairing(self):
        p=FakeProvider(assistant('', [('call','graph_overview',{})]),assistant())
        result=BoundedAgent(p,gate=lambda *a:Grant(True,True,True),tools=self.adapter().tools()).run(request(),metadata=asdict(self.scope))
        self.assertEqual(result.reason,'completed')
        returned=p.requests[1]['messages'][-1]['content'][0]
        self.assertEqual(returned['tool_use_id'],'call')
        self.assertEqual(json.loads(returned['content'][0]['text'])['scope'],asdict(self.scope))
    def test_current_callback_mutating_scope_cannot_leak_result(self):
        adapter=self.adapter()
        def current(scope):self.context['task_id']=4;return True
        adapter.current=current
        with self.assertRaises(RunStopped):adapter.call('graph_overview',self.context,{})
        self.assertFalse(self.db.calls)

    def test_rebound_graph_scope_rejected(self):
        adapter=self.adapter();self.graph.scope=replace(self.scope,run_id=2)
        with self.assertRaises(RunStopped):adapter.call('graph_overview',self.context,{})
        self.assertFalse(self.db.calls)

if __name__=='__main__':unittest.main()
