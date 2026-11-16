"""Component-only inert Bash runner; default assembly still excludes Bash."""
import unittest,tempfile
from artex.agent.bootstrap import BootstrapUnresolved
from artex.agent.runtime import Grant
from artex.tools.process import BashTool,CleanupUncertain
from artex.tools.registry import ToolRegistry
from artex.db.tool_catalog import CatalogTool
import test_inflight_cleanup as fixture

class ProcessEngineReview(unittest.TestCase):
    def test_actual_owned_worker_retains_pending_canonical_claim_and_cleanup(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);f.f.f.seed()
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);calls=[]
        def runner(context,launch):
            calls.append(launch)
            raise CleanupUncertain('synthetic process cleanup unconfirmed')
        bash=BashTool(tmp.name,shell='/bin/bash',environment={},gate=lambda *a:Grant(True,True,True),runner=runner)
        worker=f.h._child.worker
        def registry(scope,record):
            tool=bash.tools()[0]
            reg=ToolRegistry('worker',handlers=[tool],current=lambda revision:True,
                lifecycle_guard=lambda revision:f.f.f.guard(),authorize=lambda *a:Grant(True,True,True))
            reg.reload([CatalogTool(tool.name,True,tool.description,tool.input_schema,['worker'],True,'builtin',{},False)],scope.revision)
            return reg
        worker.registry_factory=registry
        f.f.f.wp.responses=[({'role':'assistant','content':[{'type':'tool_use','id':'fixture','name':'Bash','input':{'command':'inert fixture'}}]},'tool_use',{})]
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,1)
        self.assertEqual(len(calls),1);self.assertEqual(len(f.f.f.wp.calls),1)
        self.assertEqual(worker.store.get(3).node.state,'running')
        self.assertTrue(f.h.unresolved);self.assertTrue(f.f.manager._has_cleanup('5'))
        owner=f.h._child.cleanup;records=list(owner._records.values());self.assertEqual(len(records),1)
        record=records[0]
        self.assertIsNone(owner.transcript.finalized_scoped_result(record['session'],record['turn'].id,record['identity']))
        self.assertFalse(f.f.host.drain(0).all_settled)
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,2)
        self.assertEqual(len(calls),1)
