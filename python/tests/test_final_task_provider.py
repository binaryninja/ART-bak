"""Actual final journal/ChatAgent and persisted resolver; inert transport only."""
import unittest
from unittest.mock import patch
from dataclasses import replace
from artex.agent.final_task_provider import FinalTaskProviderAdapter
from artex.agent.final_assessment import FinalAssessmentRunner,CATALOG_DIGEST
from artex.agent.task_providers import TaskProviderResolver
from artex.agent.runtime import Grant
from artex.providers import Response
from artex.db.final_assessment import FinalConfiguration,AssessmentUncertain
import test_task_providers as providers
import test_final_assessment as journals

class Tests(unittest.TestCase):
    def setUp(self):
        self.p=providers.Tests();self.p.setUp();self.addCleanup(self.p.doCleanups)
        if 'stream' in self._testMethodName:self.p.rows[2]=replace(self.p.rows[2],streaming=True)
        self.j=journals.Tests()
        def configuration(model,system,revision,digest,limits):
            db=self.j.handle.executor
            for store in (self.p.tasks,self.p.agents,self.p.profiles):store.executor=db
            old=self.p.resolver
            self.p.resolver=TaskProviderResolver(self.p.tasks,self.p.agents,self.p.profiles,self.p.factory,
                current=old.current,lifecycle_guard=old.guard,authorize_task=old.authorize_task,
                authorize_profile=old.authorize_profile,allow_global=True)
            selected=self.p.resolver.select(self.p.snapshot,'planner').model
            return FinalConfiguration(selected,system,revision,CATALOG_DIGEST,limits)
        with patch.object(journals,'FinalConfiguration',side_effect=configuration):self.j.setUp()
        self.addCleanup(self.j.doCleanups)
        self.adapter=FinalTaskProviderAdapter(self.p.resolver)
        self.runner=FinalAssessmentRunner(self.j.journal,task_provider=self.adapter,
            authorize=lambda *a:Grant(True,True,True),read_authorize=lambda *a:True,persist_authorize=lambda *a:True)

    def test_actual_persisted_planner_finalizes_readonly(self):
        self.assertEqual(self.p.construct,[])
        result=self.runner.run()
        self.assertEqual(result.receipt.phase,'finalized')
        self.assertEqual(self.p.calls,[1])
        self.assertEqual(self.j.manager.deadline_owners()[0].state,'assessing')
        self.assertNotEqual(self.j.config.model.identity,self.p.resolver.select(self.p.snapshot,'goals').model.identity)

    def test_changed_profile_no_transport(self):
        self.p.rows[2]=replace(self.p.rows[2],model='changed')
        with self.assertRaises(AssessmentUncertain):self.runner.run()
        self.assertEqual(self.p.calls,[])
        self.assertEqual(self.j.manager.deadline_owners()[0].state,'quarantined')

    def test_original_configuration_grant_denial_no_transport(self):
        self.p.allowed=False
        with self.assertRaises(AssessmentUncertain):self.runner.run()
        self.assertEqual(self.p.calls,[])
        self.assertEqual(self.j.manager.deadline_owners()[0].state,'quarantined')

    def test_adapter_rebind_refused_before_transport(self):
        self.adapter.resolver=object()
        with self.assertRaises((ValueError,AssessmentUncertain)):self.runner.run()
        self.assertEqual(self.p.calls,[])

    def test_receipt_is_not_invocation(self):
        with self.assertRaises(ValueError):self.adapter.bind(self.j.lease.receipt,self.j.config.model)
        self.assertEqual(self.p.calls,[])

    def test_context_and_binding_revoked_after_model(self):
        seen=[];original=self.p.factory._transport_factory
        def factory(config):
            call=original(config)
            def invoke(*args,**kwargs):
                seen.append(self.runner._invocation)
                return call(*args,**kwargs)
            return invoke
        self.p.factory._transport_factory=factory
        self.runner.run()
        self.assertEqual(len(seen),1)
        with self.assertRaises(Exception):seen[0].validate_execution()
        with self.assertRaises(Exception):self.adapter.bind(seen[0],self.j.config.model)
        self.assertEqual(self.p.calls,[1])

    def test_stream_actual_close_once(self):
        closed=[]
        def factory(config):
            def transport(*args):
                self.p.calls.append(1)
                data=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                return Response(200,data,lambda:closed.append(1))
            return transport
        self.p.factory._transport_factory=factory
        result=self.runner.run()
        self.assertEqual(result.receipt.phase,'finalized')
        self.assertEqual(result.outcome.result.reason,'completed')
        self.assertEqual(closed,[1]);self.assertEqual(self.p.calls,[1])

    def test_stream_close_revokes_configuration_no_success(self):
        closed=[]
        def factory(config):
            def transport(*args):
                self.p.calls.append(1)
                data=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                def close():closed.append(1);self.p.allowed=False
                return Response(200,data,close)
            return transport
        self.p.factory._transport_factory=factory
        result=self.runner.run()
        self.assertNotEqual(result.outcome.result.reason,'completed')
        self.assertEqual(closed,[1]);self.assertEqual(self.p.calls,[1])

    def test_cancellation_during_transport_no_success(self):
        original=self.p.factory._transport_factory
        def factory(config):
            call=original(config)
            def transport(*args,**kwargs):
                self.runner.cancel()
                return call(*args,**kwargs)
            return transport
        self.p.factory._transport_factory=factory
        result=self.runner.run()
        self.assertEqual(result.outcome.result.reason,'cancelled')
        self.assertEqual(self.p.calls,[1])
