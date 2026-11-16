"""Independent actual journal/persisted-provider review; inert transport only."""
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from artex.agent.chat import ChatBinding
from artex.db.final_assessment import AssessmentUncertain
import test_final_task_provider as fixture

class FinalProviderReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f._testMethodName=self._testMethodName
        self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_adapter_bind_replacement_cannot_bypass_persisted_selection(self):
        f=self.f;calls=[]
        class Provider:
            def complete(self,context,request):
                calls.append(True)
                return ({'role':'assistant','content':[{'type':'text','text':'inert substituted'}]},'end_turn',{})
        f.adapter.bind=lambda invocation,model:ChatBinding(model,Provider())
        try:f.runner.run()
        except Exception:pass
        self.assertEqual(calls,[], 'Changed typed adapter bind must not gain final execution')

    def test_persisted_role_change_inside_transport_cannot_complete(self):
        from dataclasses import replace
        f=self.f;original=f.p.factory._transport_factory
        def factory(config):
            call=original(config)
            def invoke(*a,**kw):
                result=call(*a,**kw)
                f.p.rows[2]=replace(f.p.rows[2],model='changed-after-call')
                return result
            return invoke
        f.p.factory._transport_factory=factory
        result=f.runner.run()
        self.assertNotEqual(result.outcome.result.reason,'completed')
        self.assertEqual(f.p.calls,[1])
        with self.assertRaises(Exception):f.adapter.bind(f.runner._invocation,f.j.config.model)

    def test_nonreentrant_original_journal_guard_is_not_nested(self):
        import test_bootstrap_handle as bootstrap
        original=bootstrap.BootstrapCoordinator;state={'depth':0,'entries':0}
        def construct(*args,**kw):
            guard=kw['lifecycle_guard']
            @contextmanager
            def guarded(*a):
                if state['depth']:raise AssertionError('nested original journal guard')
                state['depth']+=1;state['entries']+=1
                try:
                    with guard(*a):yield
                finally:state['depth']-=1
            kw['lifecycle_guard']=guarded
            return original(*args,**kw)
        f=fixture.Tests()
        self.addCleanup(f.doCleanups)
        with patch.object(bootstrap,'BootstrapCoordinator',side_effect=construct):f.setUp()
        self.addCleanup(f.doCleanups)
        result=f.runner.run()
        self.assertEqual(result.outcome.result.reason,'completed')
        self.assertGreater(state['entries'],1);self.assertEqual(state['depth'],0)
        self.assertEqual(f.p.calls,[1])

    def test_grant_budget_exhaustion_never_constructs_transport(self):
        from artex.agent.final_assessment import FinalAssessmentRunner
        from artex.agent.runtime import Grant
        f=self.f;now=[10.]
        def authorize(*args):
            now[0]+=f.j.config.limits.max_seconds+1
            return Grant(True,True,True)
        f.runner=FinalAssessmentRunner(f.j.journal,task_provider=f.adapter,
            authorize=authorize,read_authorize=lambda *a:True,persist_authorize=lambda *a:True,
            clock=lambda:now[0])
        try:result=f.runner.run()
        except AssessmentUncertain:pass
        else:self.assertNotEqual(result.outcome.result.reason,'completed')
        self.assertEqual(f.p.calls,[]);self.assertEqual(f.p.construct,[])
        with self.assertRaises(Exception):f.runner._invocation.validate_execution()

    def test_stream_close_cancellation_closes_once_without_success(self):
        from artex.providers import Response
        f=self.f;closed=[]
        data=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        def factory(config):
            def close():closed.append(True);f.runner.cancel()
            def invoke(*a,**kw):f.p.calls.append(1);return Response(200,data,close)
            return invoke
        f.p.factory._transport_factory=factory
        result=f.runner.run()
        self.assertNotEqual(result.outcome.result.reason,'completed')
        self.assertEqual(closed,[True]);self.assertEqual(f.p.calls,[1])

    def test_retained_provider_cannot_execute_after_final_record(self):
        import artex.agent.final_task_provider as module
        f=self.f;seen=[];original=module._FinalProvider.__init__
        def init(provider,*a,**kw):original(provider,*a,**kw);seen.append(provider)
        with patch.object(module._FinalProvider,'__init__',init):f.runner.run()
        self.assertGreaterEqual(len(seen),1)
        count=len(f.p.calls)
        for provider in seen:
            with self.assertRaises(Exception):provider.complete(f.runner._invocation._context,{})
        self.assertEqual(len(f.p.calls),count)

    def test_stream_creation_and_unused_close_are_lazy(self):
        import artex.agent.final_task_provider as module
        from artex.agent.final_assessment import FinalAssessmentRunner
        from artex.agent.runtime import Grant
        f=self.f;seen=[];observed=[];original=module._FinalProvider.__init__
        def init(provider,*a,**kw):original(provider,*a,**kw);seen.append(provider)
        def grant(receipt,context,action):
            if action.kind=='model':
                before=(len(f.p.construct),len(f.p.calls),len(f.j.calls))
                generator=seen[-1].stream(context,{})
                generator.close()
                observed.append((before,(len(f.p.construct),len(f.p.calls),len(f.j.calls))))
            return Grant(True,True,True)
        f.runner=FinalAssessmentRunner(f.j.journal,task_provider=f.adapter,authorize=grant,
            read_authorize=lambda *a:True,persist_authorize=lambda *a:True)
        with patch.object(module._FinalProvider,'__init__',init):f.runner.run()
        self.assertTrue(observed)
        self.assertTrue(all(before==after for before,after in observed))
