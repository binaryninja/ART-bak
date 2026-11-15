import json,unittest
from contextlib import nullcontext
from artex.mcphttp import Client,RemoteTool,HTTPResponse
from artex.tools.mcp import MCPTools,MCPBridgeInvalid
from artex.tools.registry import ToolRegistry
from artex.agent.graph_context import ContextScope
from artex.agent.runtime import Grant,RunStopped

class Context(dict):
    cancelled=False
    def check(self):
        if self.cancelled:raise RunStopped('cancelled')

class Tests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.result={'content':[{'type':'text','text':'a'},{'type':'text','text':'b'}]}
        self.client=Client('fixture','https://fixture.invalid/mcp',transport=self.transport)
        self.client.session_id='session'
        self.scope=ContextScope(1,2,3,'revision');self.context=Context(task_id=1,exploration_id=2,run_id=3,revision='revision')
        self.remote=RemoteTool('echo','Untrusted description',{'type':'object','properties':{'x':{'type':'integer','minimum':0}},'required':['x'],'additionalProperties':False})
        self.bridge=self.build();self.name='mcp__fixture__echo'
    def transport(self,request):
        frame=json.loads(request.body);self.calls.append(frame)
        return HTTPResponse(200,{'content-type':'application/json'},json.dumps({'jsonrpc':'2.0','id':frame['id'],'result':self.result}).encode())
    def build(self,remote=None):
        return MCPTools(self.client,[remote or self.remote],self.scope,session_id='session',gate=lambda c,a:Grant(True,True,True),current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
    def call(self):return self.bridge.call(self.name,self.context,{'x':1})
    def test_constructor_inert_exact_name_and_text_concat(self):
        self.assertEqual(self.calls,[]);self.assertEqual(self.call(),'ab')
        self.assertEqual(self.calls[0]['method'],'tools/call');self.assertEqual(self.calls[0]['params'],{'name':'echo','arguments':{'x':1}})
    def test_actual_registry_compatible(self):
        registry=ToolRegistry('planner',handlers=self.bridge.tools(),current=lambda r:True,lifecycle_guard=lambda r:nullcontext(),authorize=lambda *a:Grant(True,True,True))
        registry.reload(self.bridge.catalog(agents=['planner']),'catalog-v1')
        self.assertEqual(registry.tools()[0].callback(self.context,{'x':1}),'ab');self.assertEqual(len(self.calls),1)
    def test_denial_and_cancel_no_exchange(self):
        self.bridge.gate=lambda c,a:Grant(True,True,False)
        with self.assertRaises(RunStopped):self.call()
        self.context.cancelled=True
        with self.assertRaises(RunStopped):self.call()
        self.assertEqual(self.calls,[])
    def test_binding_change_during_gate_no_exchange(self):
        def gate(c,a):self.client.session_id='other';return Grant(True,True,True)
        self.bridge.gate=gate
        with self.assertRaises(RunStopped):self.call()
        self.assertEqual(self.calls,[])
    def test_transport_rebind_during_current_no_exchange(self):
        def current(s):self.client.transport=lambda r:None;return True
        self.bridge.current=current
        with self.assertRaises(RunStopped):self.call()
        self.assertEqual(self.calls,[])
    def test_context_mismatch(self):
        self.context['task_id']=True
        with self.assertRaises(RunStopped):self.call()
        self.assertEqual(self.calls,[])
    def test_error_is_never_success_or_retried(self):
        self.result={'content':[{'type':'text','text':'sensitive remote error'}],'isError':True}
        with self.assertRaises(RunStopped) as error:self.call()
        self.assertEqual(error.exception.reason,'mcp_remote_error');self.assertNotIn('sensitive',str(error.exception));self.assertEqual(len(self.calls),1)
    def test_transport_uncertainty_no_retry_no_error_echo(self):
        def transport(request):self.calls.append(request);raise RuntimeError('secret')
        self.client.transport=transport;self.bridge=self.build()
        with self.assertRaises(RunStopped) as error:self.call()
        self.assertEqual(error.exception.reason,'mcp_outcome_uncertain');self.assertEqual(len(self.calls),1)
    def test_unsupported_and_oversize_responses_uncertain(self):
        for result in [{'content':[{'type':'image','data':'x'}]},{'content':[],'structuredContent':{}},{'content':[],'isError':1},{'content':[{'type':'text','text':'x'*70000}]}]:
            self.result=result
            with self.assertRaises(RunStopped) as error:self.call()
            self.assertEqual(error.exception.reason,'mcp_outcome_uncertain')
        self.assertEqual(len(self.calls),4)
    def test_schema_refs_combinators_and_unknown_reject_inertly(self):
        for schema in [{'type':'object','$ref':'https://invalid/schema'},{'type':'object','allOf':[]},{'type':'object','pattern':'x'},{'type':'object','properties':{'x':{'type':'array'}}}]:
            with self.assertRaises(MCPBridgeInvalid):self.build(RemoteTool('echo','',schema))
        self.assertEqual(self.calls,[])
    def test_validation_before_exchange(self):
        for args in [{'x':True},{'x':-1},{'x':1,'other':2},{}]:
            with self.assertRaises(MCPBridgeInvalid):self.bridge.call(self.name,self.context,args)
        self.assertEqual(self.calls,[])
    def test_metadata_copy_and_no_argument_gate_mutation(self):
        self.remote.input_schema['properties']['x']['type']='string'
        args={'x':1}
        def gate(c,a):args['x']='bad';return Grant(True,True,True)
        self.bridge.gate=gate
        self.bridge.call(self.name,self.context,args)
        self.assertEqual(self.calls[0]['params']['arguments'],{'x':1})

if __name__=='__main__':unittest.main()
