"""Independent inert authority/lifetime tests; no real handlers or targets."""
import unittest
from contextlib import contextmanager
from dataclasses import replace
from threading import Event,Thread,RLock
from artex.agent.runtime import Tool,Grant
from artex.db.tool_catalog import CatalogTool
from artex.tools.registry import ToolRegistry,RegistryUnavailable,RegistryConflict,RegistryDenied
from test_process import Context
SCHEMA={'type':'object','properties':{'n':{'type':'integer','minimum':1}},'required':['n'],'additionalProperties':False}

class RegistryReviewTests(unittest.TestCase):
    def setup_registry(self,callback=None,guard=None):
        self.calls=[];self.revision='r';self.approvals=[]
        @contextmanager
        def default_guard(revision):yield
        def gate(context,action):self.approvals.append(action.arguments());return Grant(True,True,True)
        handler=Tool('Fixture','inert',SCHEMA,callback or (lambda c,a:self.calls.append(a)),lambda a:set(a)=={'n'} and type(a['n']) is int and a['n']>=1)
        self.row=CatalogTool('Fixture',True,'inert',SCHEMA,['worker'],True,'builtin',{},False)
        self.registry=ToolRegistry('worker',handlers=[handler],current=lambda r:r==self.revision,lifecycle_guard=guard or default_guard,authorize=gate)
        self.registry.reload([self.row],'r');return self.registry.tools()[0]
    def test_invalid_default_never_reaches_authorization_or_callback(self):
        self.setup_registry();schema={**SCHEMA,'properties':{'n':{'type':'integer','minimum':1,'default':0}}}
        self.registry.reload([replace(self.row,schema=schema)],'r')
        with self.assertRaises(ValueError):self.registry.tools()[0].callback(Context(),{})
        self.assertEqual(self.approvals,[]);self.assertEqual(self.calls,[])
    def test_returned_and_source_schema_mutations_cannot_change_execution(self):
        tool=self.setup_registry();tool.input_schema['properties']['n']['default']=8
        with self.assertRaises(ValueError):tool.callback(Context(),{})
        self.assertEqual(self.calls,[])
    def test_validation_cannot_mutate_authorized_arguments(self):
        tool=self.setup_registry()
        def validator(args):args['n']=900;return True
        self.registry._handlers['Fixture']=replace(self.registry._handlers['Fixture'],validate=validator)
        self.registry.reload([self.row],'r');self.registry.tools()[0].callback(Context(),{'n':2})
        self.assertEqual(self.approvals,[{'n':2}]);self.assertEqual(self.calls,[{'n':2}])
    def test_reentrant_reload_refuses_and_zero_drain_reports_own_active(self):
        observed=[]
        def callback(context,args):
            with self.assertRaises(RegistryConflict):self.registry.reload([self.row],'r')
            observed.append(self.registry.drain(0))
        tool=self.setup_registry(callback);tool.callback(Context(),{'n':1})
        self.assertFalse(observed[0].drained);self.assertEqual(observed[0].active,1)
        self.assertTrue(self.registry.drain(0).drained)
        with self.assertRaises(RegistryUnavailable):tool.callback(Context(),{'n':1})
    def test_guard_waiting_call_rechecks_revision_before_any_effect(self):
        entered=Event();release=Event();block=False;errors=[]
        @contextmanager
        def guard(revision):
            if block:entered.set();release.wait(2)
            yield
        tool=self.setup_registry(guard=guard);block=True
        def run():
            try:tool.callback(Context(),{'n':1})
            except BaseException as error:errors.append(error)
        thread=Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(1));self.revision='next'
            self.assertFalse(self.registry.drain(0).drained)
        finally:release.set();thread.join(2)
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],RegistryConflict)
        self.assertEqual(self.calls,[]);self.assertEqual(self.approvals,[]);self.assertTrue(self.registry.drain(0).drained)
