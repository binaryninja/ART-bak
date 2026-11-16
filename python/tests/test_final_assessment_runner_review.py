"""Independent restricted final runner using actual journal and inert providers."""
import unittest
import test_final_assessment_runner as fixture
from test_task_goals_http import node

class FinalRunnerReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_only_allowed_read_tool_executes_and_reaches_second_model(self):
        f=self.f;calls=[];reads=[];original=f.f.db.fetch_all
        def fetch(q):
            if 'bounded_goals' in q.sql:
                reads.append(q);return [(*node(2,'goal',{'text':'known objective'},state='open'),1)]
            return original(q)
        f.f.db.fetch_all=fetch
        def complete(context,request):
            calls.append(request)
            if len(calls)==1:
                return {'role':'assistant','content':[{'type':'tool_use','id':'read1','name':'list_goals','input':{}}]},'tool_use',{}
            return {'role':'assistant','content':[{'type':'text','text':'Existing objective remains open.'}]},'end_turn',{}
        f.provider.complete=complete
        result=f.runner.run()
        self.assertEqual(len(reads),1)
        self.assertEqual(len(calls),2)
        self.assertEqual(result.outcome.result.reason,'completed')
        self.assertFalse(any(q.sql.startswith('UPDATE tasks') for q in f.f.calls))
    def test_model_grant_consumes_remaining_clock_no_transport(self):
        from artex.agent.final_assessment import FinalAssessmentRunner
        from artex.agent.runtime import Grant
        from artex.db.final_assessment import AssessmentUncertain
        f=self.f;now=[0.0]
        def approve(*args):now[0]=10000.;return Grant(True,True,True)
        runner=FinalAssessmentRunner(f.f.journal,provider_resolver=f.runner.resolver,authorize=approve,
            read_authorize=lambda r:True,persist_authorize=lambda *a:True,clock=lambda:now[0])
        try:result=runner.run()
        except AssessmentUncertain:pass
        self.assertEqual(f.calls,[])
        with self.assertRaises(Exception):runner.run()
    def test_retained_execution_context_is_revoked_after_finalization(self):
        f=self.f;contexts=[];original=f.provider.complete
        def complete(context,request):contexts.append(context);return original(context,request)
        f.provider.complete=complete
        result=f.runner.run();self.assertEqual(result.receipt.phase,'finalized')
        self.assertEqual(len(contexts),1)
        with self.assertRaises(Exception):contexts[0].check()
        self.assertFalse(f.runner.cancel())
    def test_graph_grant_revoked_during_sql_no_followup_model(self):
        from artex.agent.final_assessment import FinalAssessmentRunner
        f=self.f;allowed=[True];calls=[];original=f.f.db.fetch_all
        def fetch(q):
            if 'bounded_goals' in q.sql:
                allowed[0]=False;return [(*node(2,'goal',{'text':'private objective'},state='open'),1)]
            return original(q)
        f.f.db.fetch_all=fetch
        def complete(context,request):
            calls.append(request)
            return {'role':'assistant','content':[{'type':'tool_use','id':'read1','name':'list_goals','input':{}}]},'tool_use',{}
        f.provider.complete=complete
        runner=FinalAssessmentRunner(f.f.journal,provider_resolver=f.runner.resolver,authorize=f.runner.authorize,
            read_authorize=lambda r:allowed[0],persist_authorize=lambda *a:True)
        result=runner.run()
        self.assertEqual(len(calls),1)
        self.assertNotIn('private objective',str(result.outcome.result.messages))
        self.assertNotEqual(result.outcome.result.reason,'completed')
    def test_final_journal_callback_cannot_reuse_execution_context(self):
        f=self.f;contexts=[];checks=[];original=f.provider.complete;record=f.f.journal.record_finalized
        def complete(context,request):contexts.append(context);return original(context,request)
        def finalizing(*a):
            with self.assertRaises(Exception):contexts[0].check()
            checks.append(True);return record(*a)
        f.provider.complete=complete;f.f.journal.record_finalized=finalizing
        result=f.runner.run()
        self.assertEqual(checks,[True]);self.assertEqual(result.receipt.phase,'finalized')
