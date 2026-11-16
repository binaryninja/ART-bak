"""Final-provider seam only, inert providers/SQL and synthetic positive clock.

Normal positive-duration admission and assembly wiring remain disabled.
"""
from dataclasses import replace
import unittest
from artex.agent.chat import ChatBinding
from artex.agent.deadline import PreparedDispatchClock,DispatchClockUnavailable,DispatchClockUncertain
from artex.agent.task_deadline_provider import DeadlineProvider
from artex.agent.runtime import RuntimeContext,RunStopped,Grant
import test_owned_generation_clock as fixture


class Provider:
    def __init__(self):self.calls=[];self.closed=False;self.hook=lambda:None
    def complete(self,context,request):
        self.calls.append((context,request,context.remaining_seconds()));self.hook()
        return 'inert-result'
    def stream(self,context,request):
        self.calls.append((context,request,context.remaining_seconds()))
        try:
            self.hook();yield {'type':'inert-first'}
            self.hook();yield {'type':'inert-second'}
        finally:self.closed=True


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.provider=Provider();self.allowed=True
        self.token=type('Token',(),{'cancelled':False})()
        self.clock=self.f.f.helper.monotonic_clock
        self.context=RuntimeContext(dict(self.f.f.ctx),self.token,160.,self.clock)
        self.adapter=self.make()
    def make(self,role='planner',streaming=False):
        model=replace(self.f.f.helper.model,streaming=streaming)
        helper=PreparedDispatchClock(self.f.f.f.store,model=model,role=role,timeout_seconds=10,
            authorize=lambda *a:Grant(True,True,True),binding_current=lambda m:self.allowed,
            wall_clock=lambda:self.f.f.wall,monotonic_clock=self.clock,generation_clock=self.f.shared)
        return DeadlineProvider(ChatBinding(model,self.provider),helper)
    def stamps(self):return sum(q.sql.startswith('UPDATE tasks SET first_run_at') for q in self.f.f.f.sql)
    def test_constructor_inert_and_actual_context_clamped(self):
        self.assertEqual(self.stamps(),0);self.assertEqual(self.provider.calls,[])
        self.assertEqual(self.adapter.complete(self.context,{'messages':[]}),'inert-result')
        self.assertEqual(self.stamps(),1)
        self.assertIs(self.provider.calls[0][0],self.context)
        self.assertEqual(self.provider.calls[0][2],10.)
    def test_two_role_adapters_share_single_stamp(self):
        self.adapter.complete(self.context,{})
        worker=self.make('worker');self.context['role']='worker';self.f.f.mono+=2
        worker.complete(self.context,{})
        self.assertEqual(self.stamps(),1);self.assertEqual(self.provider.calls[-1][2],8.)
    def test_stream_creation_and_unstarted_close_are_inert(self):
        self.adapter=self.make(streaming=True)
        stream=self.adapter.stream(self.context,{})
        self.assertEqual(self.stamps(),0);self.assertEqual(self.provider.calls,[])
        stream.close()
        self.assertEqual(self.stamps(),0);self.assertEqual(self.provider.calls,[])
    def test_stream_first_next_clamps_and_expiry_closes(self):
        self.adapter=self.make(streaming=True)
        stream=self.adapter.stream(self.context,{})
        self.assertEqual(next(stream),{'type':'inert-first'});self.assertEqual(self.stamps(),1)
        self.f.f.mono+=11
        with self.assertRaises(RunStopped):next(stream)
        self.assertTrue(self.provider.closed);self.assertEqual(len(self.provider.calls),1)
    def test_mismatched_clock_rejected_before_stamp(self):
        other=RuntimeContext(dict(self.f.f.ctx),self.token,160.,lambda:100.)
        with self.assertRaises(DispatchClockUnavailable):self.adapter.complete(other,{})
        self.assertEqual(self.stamps(),0);self.assertEqual(self.provider.calls,[])
    def test_non_runtime_context_rejected_before_stamp(self):
        with self.assertRaises(DispatchClockUnavailable):self.adapter.complete(self.f.f.ctx,{})
        self.assertEqual(self.stamps(),0)
    def test_foreign_role_denied_without_transport(self):
        self.context['role']='goals'
        with self.assertRaises(DispatchClockUnavailable):self.adapter.complete(self.context,{})
        self.assertEqual(self.stamps(),0);self.assertEqual(self.provider.calls,[])
    def test_provider_change_rejected_before_stamp(self):
        self.provider.complete=lambda *a:'replacement'
        with self.assertRaises(DispatchClockUnavailable):self.adapter.complete(self.context,{})
        self.assertEqual(self.stamps(),0)
    def test_callback_change_rejected_before_stamp(self):
        self.adapter.dispatch_clock.binding_current=lambda m:True
        with self.assertRaises(DispatchClockUnavailable):self.adapter.complete(self.context,{})
        self.assertEqual(self.stamps(),0)
    def test_revoked_by_stamp_no_provider_call_or_retry(self):
        store=self.f.f.f.store;original=store.stamp_owned
        def stamp(*a):
            value=original(*a);self.allowed=False;return value
        store.stamp_owned=stamp
        with self.assertRaises(DispatchClockUnavailable):self.adapter.complete(self.context,{})
        self.assertEqual(self.provider.calls,[]);self.assertEqual(self.stamps(),1)
        self.allowed=True
        with self.assertRaises(DispatchClockUncertain):self.adapter.complete(self.context,{})
        self.assertEqual(self.stamps(),1)
    def test_stream_revocation_does_not_deliver_next_event_and_closes(self):
        self.adapter=self.make(streaming=True)
        stream=self.adapter.stream(self.context,{})
        next(stream);self.allowed=False
        with self.assertRaises(DispatchClockUnavailable):next(stream)
        self.assertTrue(self.provider.closed)
    def test_smaller_own_budget_preserved(self):
        self.context.clamp_deadline(103.,clock=self.clock)
        self.adapter.complete(self.context,{})
        self.assertEqual(self.provider.calls[0][2],3.)
    def test_expiry_inside_provider_rejects_result(self):
        self.provider.hook=lambda:setattr(self.f.f,'mono',111.)
        with self.assertRaises(RunStopped):self.adapter.complete(self.context,{})
        self.assertEqual(len(self.provider.calls),1)
    def test_arbitrary_facade_rejected(self):
        with self.assertRaises(ValueError):DeadlineProvider(self.adapter.binding,object())
    def test_cancel_before_lazy_first_next_does_not_stamp(self):
        self.adapter=self.make(streaming=True)
        stream=self.adapter.stream(self.context,{})
        self.token.cancelled=True
        with self.assertRaises(RunStopped):next(stream)
        self.assertEqual(self.stamps(),0);self.assertEqual(self.provider.calls,[])
    def test_actual_stream_close_failure_surfaces(self):
        class BrokenClose(Provider):
            def stream(inner,context,request):
                class Iterator:
                    def __iter__(self):return self
                    def __next__(self):return {'type':'inert'}
                    def close(self):raise RuntimeError('inert close failure')
                return Iterator()
        self.provider=BrokenClose();self.adapter=self.make(streaming=True)
        stream=self.adapter.stream(self.context,{})
        next(stream)
        with self.assertRaisesRegex(RuntimeError,'close failure'):stream.close()
    def test_unshared_facade_rejected(self):
        helper=self.f.f.make()
        with self.assertRaises(ValueError):DeadlineProvider(ChatBinding(helper.model,self.provider),helper)
    def test_stream_close_revocation_is_not_clean_exhaustion(self):
        outer=self
        class RevokingClose(Provider):
            def stream(inner,context,request):
                class Iterator:
                    def __iter__(self):return self
                    def __next__(self):raise StopIteration
                    def close(self):
                        inner.closed=True
                        outer.allowed=False
                return Iterator()
        self.provider=RevokingClose();self.adapter=self.make(streaming=True)
        stream=self.adapter.stream(self.context,{})
        with self.assertRaises(DispatchClockUnavailable):next(stream)
        self.assertTrue(self.provider.closed)
    def test_stream_close_rebinding_is_not_clean_exhaustion(self):
        outer=self
        class RebindingClose(Provider):
            def stream(inner,context,request):
                class Iterator:
                    def __iter__(self):return self
                    def __next__(self):raise StopIteration
                    def close(self):
                        inner.closed=True
                        outer.adapter.binding=ChatBinding(outer.adapter.binding.model,Provider())
                return Iterator()
        self.provider=RebindingClose();self.adapter=self.make(streaming=True)
        stream=self.adapter.stream(self.context,{})
        with self.assertRaises(DispatchClockUnavailable):next(stream)
        self.assertTrue(self.provider.closed)
    def test_distinct_role_models_new_contexts_share_one_stamp(self):
        planner_model=replace(self.adapter.binding.model,identity='planner-version-A',model='planner-model',profile_id=11)
        worker_model=replace(self.adapter.binding.model,identity='worker-version-B',model='worker-model',profile_id=22)
        def adapter(role,model):
            helper=PreparedDispatchClock(self.f.f.f.store,model=model,role=role,timeout_seconds=10,
                authorize=lambda *a:Grant(True,True,True),binding_current=lambda m:m==model,
                wall_clock=lambda:self.f.f.wall,monotonic_clock=self.clock,generation_clock=self.f.shared)
            return DeadlineProvider(ChatBinding(model,Provider()),helper)
        planner=adapter('planner',planner_model);worker=adapter('worker',worker_model)
        planner.complete(self.context,{})
        self.f.f.mono+=2
        worker_context=RuntimeContext({**dict(self.f.f.ctx),'role':'worker'},self.token,170.,self.clock)
        worker.complete(worker_context,{})
        self.assertEqual(self.stamps(),1)
        self.assertEqual(worker_context.remaining_seconds(),8.)
        self.assertNotEqual(planner.binding.model.identity,worker.binding.model.identity)
        with self.assertRaises(ValueError):DeadlineProvider(planner.binding,worker.dispatch_clock)
        with self.assertRaises(ValueError):DeadlineProvider(worker.binding,planner.dispatch_clock)
        with self.assertRaises(DispatchClockUnavailable):planner.complete(worker_context,{})
        self.assertEqual(len(planner.binding.provider.calls),1)
        self.assertEqual(len(worker.binding.provider.calls),1)
    def test_explicit_close_rechecks_and_closes_exactly_once(self):
        outer=self;closed=[]
        class Revoker(Provider):
            def stream(inner,context,request):
                try:yield {'type':'inert'}
                finally:
                    closed.append(True);outer.allowed=False
        self.provider=Revoker();self.adapter=self.make(streaming=True)
        stream=self.adapter.stream(self.context,{})
        next(stream)
        with self.assertRaises(DispatchClockUnavailable):stream.close()
        stream.close()
        self.assertEqual(closed,[True])
    def test_original_stream_failure_preserved_after_close_revocation(self):
        outer=self;closed=[]
        class Broken(Provider):
            def stream(inner,context,request):
                try:
                    raise RuntimeError('original uncertain transport')
                    yield
                finally:
                    closed.append(True);outer.allowed=False
        self.provider=Broken();self.adapter=self.make(streaming=True)
        with self.assertRaisesRegex(RuntimeError,'original uncertain transport'):
            next(self.adapter.stream(self.context,{}))
        self.assertEqual(closed,[True])
