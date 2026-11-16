"""Compose real process ownership before actual bootstrap installs its child.

Synthetic positive pins remain confined to the existing frontier fixture. The
injected runner is authored/inert; no subprocesses, live models or targets.
"""
from unittest.mock import patch
from artex.agent.engine import TaskEngineHandle
from artex.agent.task_process import WorkerProcessPolicy,PreparedWorkerProcess
from artex.agent.runtime import Grant
from artex.tools.registry import ToolRegistry
from artex.db.tool_catalog import CatalogTool
import test_bootstrap_handle as bootstrap
import test_frontier_completion as frontier


def make(test,root,runner):
    original=bootstrap.BootstrapHandleTests.setUp;captured=[]
    policy=WorkerProcessPolicy('frontier-process',root,'/bin/bash',(),runner,lambda *a:Grant(True,True,True))
    def setup(f):
        original(f);old=f.child_factory;captured.append(f)
        def child(row,token,scope,current,guard):
            base=old(row,token,scope,current,guard)
            base.worker.current=lambda s,p:current(scope,token)
            process=PreparedWorkerProcess(scope,policy,configuration_current=lambda s:True)
            def registry(s,record):
                tools=process.tools(s);t=tools[0]
                reg=ToolRegistry('worker',handlers=tools,current=lambda revision:True,
                    lifecycle_guard=lambda revision:f.f.guard(),authorize=lambda *a:Grant(True,True,True))
                reg.reload([CatalogTool(t.name,True,t.description,t.input_schema,['worker'],True,'builtin',{},False)],s.revision)
                return reg
            base.worker.registry_factory=registry
            return TaskEngineHandle(scope,token,store=base.store,planner=base.planner,planner_scope=base.planner_scope,
                worker=base.worker,worker_model=base.worker_model,worker_system=base.worker_system,owner=base.owner,
                current=current,lifecycle_guard=guard,single_process=True,cleanup_authorize=lambda *a:True,
                cleanup_current=lambda s:True,process=process)
        f.child_factory=child
    with patch.object(bootstrap.BootstrapHandleTests,'setUp',setup):
        f=frontier.Tests();f.setUp()
    test.addCleanup(f.doCleanups)
    b=captured[0];b.f.seed()
    oldfetch=b.db.fetch_all
    def fetch(q):
        if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):
            r=b.db.receipt;return [(5,)] if r is not None and tuple(r[:7])==q.params else []
        return oldfetch(q)
    b.db.fetch_all=fetch
    b.f.wp.responses=[({'role':'assistant','content':[{'type':'tool_use','id':'inert','name':'Bash',
        'input':{'command':'inert fixture'}}]},'tool_use',{}),
        ({'role':'assistant','content':[{'type':'text','text':'done'}]},'end_turn',{})]
    # Bind while original owner is idle and before any process uncertainty.
    f.host.runner.bind_frontier_completion(f.handle,f.component)
    return f,b
