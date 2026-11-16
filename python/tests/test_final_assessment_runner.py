import unittest
from unittest.mock import patch
from artex.agent.final_assessment import FinalAssessmentRunner,CATALOG_DIGEST
from artex.agent.chat import ChatBinding
from artex.agent.runtime import Grant
from artex.db.final_assessment import FinalConfiguration,AssessmentConflict,AssessmentUncertain
import test_final_assessment as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests()
        def config(model,system,policy,digest,limits):return FinalConfiguration(model,system,policy,CATALOG_DIGEST,limits)
        with patch.object(fixture,'FinalConfiguration',side_effect=config):self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.calls=[];self.allowed=True
        owner=self
        class Provider:
            def complete(self,context,request):
                owner.calls.append(request)
                return {'role':'assistant','content':[{'type':'text','text':'Existing goals inspected; no completion claimed.'}]},'end_turn',{}
        self.provider=Provider()
        self.runner=FinalAssessmentRunner(self.f.journal,
            provider_resolver=lambda r,m:ChatBinding(m,self.provider),
            authorize=lambda *a:Grant(self.allowed,True,True),read_authorize=lambda r:True,persist_authorize=lambda *a:True)
    def test_actual_canonical_turn_finalizes_without_task_completion(self):
        self.assertEqual(self.calls,[]);result=self.runner.run()
        self.assertEqual(result.receipt.phase,'finalized');self.assertTrue(result.outcome.persisted)
        self.assertEqual(len(self.calls),1)
        self.assertEqual([t['name'] for t in self.calls[0]['tools']],['list_goals'])
        self.assertFalse(self.f.manager.complete('5',self.f.handle.token,'done'))
        with self.assertRaises(AssessmentConflict):self.runner.run()
        self.assertEqual(len(self.calls),1)
    def test_existing_pending_never_runs(self):
        self.f.journal.reserve()
        with self.assertRaises(AssessmentUncertain):self.runner.run()
        self.assertEqual(self.calls,[])
    def test_denied_model_grant_no_provider(self):
        self.allowed=False
        result=self.runner.run()
        self.assertEqual(result.receipt.phase,'finalized');self.assertEqual(self.calls,[])
    def test_provider_failure_never_replays(self):
        self.provider.complete=lambda *a:(_ for _ in ()).throw(RuntimeError('private'))
        result=self.runner.run()
        self.assertEqual(result.receipt.phase,'finalized')
        with self.assertRaises(AssessmentConflict):self.runner.run()
    def test_persistence_denial_retains_owner(self):
        self.runner.persist_authorize=lambda *a:False
        # Rebinding is denied even before journal allocation.
        with self.assertRaises(AssessmentUncertain):self.runner.run()
        self.assertEqual(self.calls,[])
    def test_actual_read_tool_queries_only_bounded_goals(self):
        original=self.f.db.fetch_all;reads=[]
        def fetch(q):
            if 'FROM exploration_nodes WHERE exploration_id=' in q.sql:
                reads.append(q);return []
            return original(q)
        self.f.db.fetch_all=fetch
        ordinary=self.provider.complete
        def complete(context,request):
            if not self.calls:
                self.calls.append(request)
                return {'role':'assistant','content':[{'type':'tool_use','id':'read1','name':'list_goals','input':{}}]},'tool_use',{}
            return ordinary(context,request)
        self.provider.complete=complete
        result=self.runner.run()
        self.assertEqual(result.outcome.result.tool_calls,1)
        self.assertEqual(len(reads),1);self.assertEqual(reads[0].params[0],self.f.lease.receipt.exploration_id)
        self.assertEqual(result.outcome.result.reason,'completed')
    def test_cancel_during_provider_commits_outcome_without_terminal_write(self):
        ordinary=self.provider.complete
        def complete(context,request):
            self.assertTrue(self.runner.cancel());return ordinary(context,request)
        self.provider.complete=complete
        result=self.runner.run()
        self.assertEqual(result.receipt.phase,'finalized')
        self.assertEqual(result.outcome.result.reason,'cancelled')
        self.assertFalse(self.runner.cancel())
        self.assertEqual(self.f.manager.deadline_owners()[0].state,'assessing')
