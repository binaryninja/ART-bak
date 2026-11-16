"""Actual owned chain and inert runner only: no subprocess is created."""
import tempfile,unittest
from artex.agent.task_process import WorkerProcessPolicy,PreparedWorkerProcess,ProcessBindingUnavailable
from artex.agent.engine import TaskEngineHandle
from artex.agent.bootstrap import BootstrapUnresolved
from artex.agent.runtime import Grant
from artex.tools.process import ProcessResult
from artex.tools.registry import ToolRegistry
from artex.db.tool_catalog import CatalogTool
import test_bootstrap_handle as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.BootstrapHandleTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.calls=[];self.grants=[];self.hook=lambda c,l:ProcessResult(b'inert',0);self.allowed=True
        def runner(c,l):self.calls.append(l);return self.hook(c,l)
        def grant(c,l):self.grants.append(l);return Grant(True,self.allowed,True)
        self.policy=WorkerProcessPolicy('process-v1',self.tmp.name,'/bin/bash',(),runner,grant)
        old=self.f.child_factory
        def child(row,token,scope,current,guard):
            base=old(row,token,scope,current,guard)
            base.worker.current=lambda s,p:current(scope,token)
            self.process=PreparedWorkerProcess(scope,self.policy,configuration_current=lambda s:True)
            def registry(s,record):
                tools=self.process.tools(s);t=tools[0]
                reg=ToolRegistry('worker',handlers=tools,current=lambda revision:True,
                    lifecycle_guard=lambda revision:self.f.f.guard(),authorize=lambda *a:Grant(True,True,True))
                reg.reload([CatalogTool(t.name,True,t.description,t.input_schema,['worker'],True,'builtin',{},False)],s.revision)
                return reg
            base.worker.registry_factory=registry
            return TaskEngineHandle(scope,token,store=base.store,planner=base.planner,planner_scope=base.planner_scope,
                worker=base.worker,worker_model=base.worker_model,worker_system=base.worker_system,owner=base.owner,
                current=current,lifecycle_guard=guard,single_process=True,cleanup_authorize=lambda p:True,
                cleanup_current=lambda s:True,process=self.process)
        self.f.child_factory=child
        self.h=self.f.start();self.f.advance(self.h)
        oldfetch=self.f.db.fetch_all
        def fetch(q):
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):
                r=self.f.db.receipt;return [(5,)] if r is not None and tuple(r[:7])==q.params else []
            return oldfetch(q)
        self.f.db.fetch_all=fetch
        self.f.f.seed()
        self.f.f.wp.responses=[({'role':'assistant','content':[{'type':'tool_use','id':'inert','name':'Bash','input':{'command':'inert fixture'}}]},'tool_use',{}),
                              ({'role':'assistant','content':[{'type':'text','text':'done'}]},'end_turn',{})]
    def test_owned_success_and_exact_launch(self):
        self.assertEqual(self.calls,[])
        self.f.advance(self.h,1)
        self.assertEqual(self.grants,self.calls*2);self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0].cwd,self.tmp.name);self.assertEqual(self.calls[0].environment,())
        self.assertTrue(self.process.drained());self.assertFalse(self.h.unresolved)
        self.assertEqual(self.h._child.worker.store.get(3).node.state,'done')
    def test_denied_launch_does_not_create_effect(self):
        self.allowed=False;self.f.advance(self.h,1)
        self.assertEqual(self.calls,[]);self.assertTrue(self.process.drained())
    def test_exception_sticky_independent_of_canonical_cleanup(self):
        def fail(c,l):raise RuntimeError('inert uncertain return')
        self.hook=fail
        with self.assertRaises(BootstrapUnresolved):self.f.advance(self.h,1)
        self.assertTrue(self.process.unresolved());self.assertFalse(self.process.drained())
        self.assertFalse(self.h.wait(0));self.assertTrue(self.h.unresolved)
        self.assertEqual(self.h._child.worker.store.get(3).node.state,'running')
        with self.assertRaises(BootstrapUnresolved):self.f.advance(self.h,2)
        self.assertEqual(len(self.calls),1)
    def test_invalid_result_is_uncertain_not_normal_tool_error(self):
        self.hook=lambda c,l:ProcessResult(b'x'*65537,0)
        with self.assertRaises(BootstrapUnresolved):self.f.advance(self.h,1)
        self.assertFalse(self.h.wait(0));self.assertTrue(self.process.unresolved())
    def test_cancellation_raising_from_runner_is_uncertain(self):
        def cancel(c,l):self.f.host.pause('5',timeout=0);c.check()
        self.hook=cancel
        with self.assertRaises(BootstrapUnresolved):self.f.advance(self.h,1)
        self.assertFalse(self.h.wait(0));self.assertTrue(self.process.unresolved())
    def test_policy_replacement_fails_before_runner(self):
        object.__setattr__(self.policy,'runner',lambda *a:ProcessResult(b'wrong',0))
        with self.assertRaises(Exception):self.f.advance(self.h,1)
        self.assertEqual(self.calls,[])
    def test_active_ledger_remains_visible_during_runner(self):
        observed=[]
        def run(c,l):observed.append(self.process.drained());return ProcessResult(b'ok',0)
        self.hook=run;self.f.advance(self.h,1)
        self.assertEqual(observed,[False]);self.assertTrue(self.process.drained())
    def test_blocked_runner_prevents_outer_wait_until_verified_return(self):
        from threading import Event,Thread
        entered=Event();release=Event();errors=[]
        def run(c,l):entered.set();release.wait(3);return ProcessResult(b'ok',0)
        self.hook=run
        def advance():
            try:self.f.advance(self.h,1)
            except BaseException as exc:errors.append(exc)
        thread=Thread(target=advance);thread.start()
        try:
            self.assertTrue(entered.wait(1));self.assertFalse(self.process.drained())
            self.assertFalse(self.h.wait(0))
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertTrue(self.h.wait(0));self.assertTrue(self.process.drained())
