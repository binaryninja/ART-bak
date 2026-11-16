"""Explicit assembly wiring, inert construction only; no command execution."""
import tempfile,unittest
from contextlib import nullcontext
from dataclasses import replace
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.agent.task_process import WorkerProcessPolicy
from artex.agent.engine import EngineScope
from artex.agent.worker import WorkerScope
from artex.agent.runtime import Grant
from artex.manager import CancellationToken,RuntimeUnavailable
from artex.tools.process import BashTool,ProcessResult
from artex.db.tool_catalog import CatalogTool
import test_task_runtime as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.calls=[]
        self.policy=WorkerProcessPolicy('p1',self.tmp.name,'/bin/bash',(),lambda *a:self.calls.append(a) or ProcessResult(b'',0),lambda *a:Grant(True,True,True))
        t=BashTool(self.tmp.name,shell='/bin/bash',environment={},gate=self.policy.authorize,runner=self.policy.runner).tools()[0]
        self.row=CatalogTool(t.name,True,t.description,t.input_schema,['worker'],True,'builtin',{},False)
    def make(self,policy=None,cleanup=True,catalog=True):
        a=self.f.assembly
        return TaskRuntimeAssembly(a.executor,providers=a.providers,transcript=a.transcript,prompts=a._prompts,
            policy_revision=a._revision,current=a._current,lifecycle_guard=a._guard,authorize=a._authorize,
            asset_authorize=a._assets,validate_input=a._input,graph_catalog={'planner':[],'worker':[self.row] if catalog else []},
            cleanup_authorize=(lambda *a:True) if cleanup else None,worker_process_policy=policy)
    def engine(self,a):
        conf=a.bootstrap_configuration(self.f.p.snapshot)
        return a.engine_factory(self.f.p.snapshot,CancellationToken(),EngineScope(5,9,71,conf.policy_revision),lambda *a:True,lambda *a:nullcontext())
    def registry(self,e):
        s=e.scope
        ws=WorkerScope(s.task_id,s.exploration_id,s.run_id,s.revision,3,0,'fixture',e.worker_model,e.worker_system)
        return e.worker.registry_factory(ws,None)
    def test_default_assembly_still_rejects_bash_catalog(self):
        e=self.engine(self.make())
        with self.assertRaises(RuntimeUnavailable):self.registry(e)
        self.assertIsNone(e.process);self.assertEqual(self.calls,[])
    def test_optin_exposes_only_explicit_worker_catalog(self):
        e=self.engine(self.make(self.policy))
        self.assertEqual([t.name for t in self.registry(e).tools()],['Bash'])
        self.assertIs(e.process.policy,self.policy);self.assertEqual(self.calls,[])
        empty=self.engine(self.make(self.policy,catalog=False))
        self.assertEqual(self.registry(empty).tools(),())
    def test_requires_exact_policy_and_cleanup(self):
        with self.assertRaises(ValueError):self.make(object())
        with self.assertRaises(ValueError):self.make(self.policy,False)
    def test_configuration_changes_and_rebinding_rejected(self):
        a=self.make(self.policy);one=a.bootstrap_configuration(self.f.p.snapshot)
        b=self.make(replace(self.policy,revision='p2'));two=b.bootstrap_configuration(self.f.p.snapshot)
        self.assertNotEqual(one.policy_revision,two.policy_revision)
        object.__setattr__(self.policy,'authorize',lambda *a:Grant(True,True,True))
        with self.assertRaises(RuntimeUnavailable):a.validate_bindings()
