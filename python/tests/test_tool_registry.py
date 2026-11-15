import unittest
from contextlib import contextmanager
from dataclasses import replace
from threading import Event,Thread
from artex.tools.registry import ToolRegistry,RegistryDenied,RegistryConflict,RegistryUnavailable
from artex.agent.runtime import Tool,Grant
from artex.db.tool_catalog import CatalogTool
from test_process import Context
SCHEMA={'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}

class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.actions=[];self.revision='r1'
        def callback(context,args):self.calls.append(args);return args['text']
        def validate(args):return set(args)=={'text'} and isinstance(args['text'],str)
        self.handler=Tool('Echo','code',SCHEMA,callback,validate)
        self.row=CatalogTool('Echo',True,'catalog',SCHEMA,['worker'],True,'builtin',{},False)
        @contextmanager
        def guard(revision):yield
        self.guard=guard
        def authorize(context,action):self.actions.append(action);return Grant(True,True,True)
        self.registry=ToolRegistry('worker',handlers=[self.handler],current=lambda rev:rev==self.revision,lifecycle_guard=guard,authorize=authorize)
    def load(self,row=None):self.registry.reload([row or self.row],self.revision);return self.registry.tools()[0]
    def test_actual_explicit_callback_and_annotation_defaults(self):
        schema={'type':'object','properties':{'text':{'type':'string','default':'default','description':'edited'}},'required':['text'],'additionalProperties':False}
        tool=self.load(replace(self.row,schema=schema))
        self.assertEqual(tool.callback(Context(),{}),'default');self.assertEqual(self.calls,[{'text':'default'}]);self.assertEqual(self.actions[0].arguments(),self.calls[0])
    def test_enabled_not_permission(self):
        tool=self.load();self.registry.authorize=lambda *a:Grant(True,True,False)
        with self.assertRaises(RegistryDenied):tool.callback(Context(),{'text':'x'})
        self.assertEqual(self.calls,[])
    def test_mutating_gate_cannot_change_execution(self):
        tool=self.load()
        def gate(c,a):a.arguments()['text']='changed';return Grant(True,True,True)
        self.registry.authorize=gate
        self.assertEqual(tool.callback(Context(),{'text':'original'}),'original')
    def test_disabled_and_unbound_omitted(self):
        self.registry.reload([replace(self.row,enabled=False)],'r1');self.assertEqual(self.registry.tools(),())
        self.registry.reload([replace(self.row,agents=['planner'])],'r1');self.assertEqual(self.registry.tools(),())
    def test_deferred_unlock_is_not_approval(self):
        self.registry.reload([replace(self.row,deferred=True)],'r1');self.assertEqual(self.registry.tools(),())
        self.registry.unlock('Echo','r1');self.registry.authorize=lambda *a:None
        with self.assertRaises(RegistryDenied):self.registry.tools()[0].callback(Context(),{'text':'x'})
    def test_custom_exec_and_changed_schema_unsupported(self):
        for row in (replace(self.row,kind='command',exec={'command':'never run'}),replace(self.row,schema={'type':'string'})):
            with self.assertRaises(RegistryUnavailable):self.registry.reload([row],'r1')
        self.assertEqual(self.calls,[])
    def test_stale_handles_and_dynamic_revision(self):
        tool=self.load();self.revision='r2'
        with self.assertRaises(RegistryConflict):tool.callback(Context(),{'text':'x'})
        self.registry.reload([self.row],'r2')
        with self.assertRaises(RegistryUnavailable):tool.callback(Context(),{'text':'x'})
    def test_change_during_gate_blocks(self):
        tool=self.load()
        def gate(c,a):self.revision='r2';return Grant(True,True,True)
        self.registry.authorize=gate
        with self.assertRaises(RegistryConflict):tool.callback(Context(),{'text':'x'})
        self.assertEqual(self.calls,[])
    def test_explicit_drain_reload_and_active_counts(self):
        entered=Event();release=Event()
        def block(context,args):entered.set();release.wait(2);return 'done'
        self.registry._handlers['Echo']=replace(self.handler,callback=block)
        tool=self.load();thread=Thread(target=lambda:tool.callback(Context(),{'text':'x'}));thread.start()
        try:
            self.assertTrue(entered.wait(1));self.assertFalse(self.registry.drain(0).drained)
            with self.assertRaises(RegistryConflict):self.registry.reload([self.row],'r1')
            with self.assertRaises(RegistryUnavailable):tool.callback(Context(),{'text':'y'})
        finally:release.set();thread.join(2)
        self.assertTrue(self.registry.drain(0).drained);self.assertEqual(self.registry.tools(),())
        self.registry.reload([self.row],'r1');self.assertEqual(len(self.registry.tools()),1)
    def test_baseexception_releases_owned_call(self):
        class Interrupted(BaseException):pass
        def fail(c,a):raise Interrupted()
        self.registry._handlers['Echo']=replace(self.handler,callback=fail);tool=self.load()
        with self.assertRaises(Interrupted):tool.callback(Context(),{'text':'x'})
        self.assertTrue(self.registry.drain(0).drained)
    def test_real_process_handler_bridge_with_inert_runner(self):
        import tempfile
        from artex.tools.process import BashTool,ProcessResult
        with tempfile.TemporaryDirectory() as root:
            launches=[]
            def run(context,launch):launches.append(launch);return ProcessResult(b'fixture result',0)
            bash=BashTool(root,shell='/bin/bash',environment={},gate=lambda *args:Grant(True,True,True),runner=run).tools()[0]
            row=CatalogTool('Bash',True,bash.description,bash.input_schema,['worker'],True,'builtin',{},False)
            registry=ToolRegistry('worker',handlers=[bash],current=lambda r:r=='r',lifecycle_guard=self.guard,authorize=lambda *a:Grant(True,True,True))
            registry.reload([row],'r')
            self.assertEqual(registry.tools()[0].callback(Context(),{'command':'inert fixture'}),'fixture result')
            self.assertEqual(len(launches),1);self.assertEqual(launches[0].environment,())
