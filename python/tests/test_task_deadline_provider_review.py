"""Independent shared-generation provider checks; inert transport and SQL."""
import unittest
from datetime import timedelta
from artex.agent.runtime import RuntimeContext
from artex.agent.deadline import DispatchClockUnavailable
import test_task_deadline_provider as fixture

class DeadlineProviderReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_new_worker_context_and_backward_wall_never_expand_shared_ceiling(self):
        f=self.f;f.adapter.complete(f.context,{})
        f.f.f.mono+=2;f.f.f.wall-=timedelta(hours=1)
        worker=f.make('worker')
        metadata=dict(f.context);metadata['role']='worker'
        fresh=RuntimeContext(metadata,f.token,1000.,f.clock)
        worker.complete(fresh,{})
        self.assertEqual(f.provider.calls[-1][2],8.)
        self.assertEqual(f.stamps(),1)
    def test_ambiguous_stamp_blocks_other_role_without_second_attempt(self):
        f=self.f;store=f.f.f.f.store;original=store.stamp_owned
        def stamp(*args):
            original(*args)
            raise RuntimeError('inert ambiguous postwrite')
        store.stamp_owned=stamp
        with self.assertRaises(RuntimeError):f.adapter.complete(f.context,{})
        worker=f.make('worker');metadata=dict(f.context);metadata['role']='worker'
        fresh=RuntimeContext(metadata,f.token,1000.,f.clock)
        with self.assertRaises(DispatchClockUnavailable):worker.complete(fresh,{})
        self.assertEqual(f.stamps(),1);self.assertEqual(f.provider.calls,[])
    def test_lazy_stream_revalidates_replaced_transport_before_first_next(self):
        f=self.f;adapter=f.make(streaming=True);stream=adapter.stream(f.context,{})
        f.provider.stream=lambda *args:iter(())
        from artex.agent.deadline import DispatchClockUnavailable
        with self.assertRaises(DispatchClockUnavailable):next(stream)
        self.assertEqual(f.stamps(),0);self.assertEqual(f.provider.calls,[])
    def test_original_iterator_error_identity_survives_revoking_close_once(self):
        f=self.f;closed=[];original=RuntimeError('synthetic transport interruption')
        class Iterator:
            def __iter__(self):return self
            def __next__(self):raise original
            def close(self):
                closed.append(True);f.allowed=False
        class Provider:
            def stream(self,*args):return Iterator()
        f.provider=Provider();adapter=f.make(streaming=True);stream=adapter.stream(f.context,{})
        with self.assertRaises(RuntimeError) as raised:next(stream)
        self.assertIs(raised.exception,original)
        stream.close();self.assertEqual(closed,[True])
    def test_successful_explicit_close_cannot_hide_clock_expiry(self):
        f=self.f;closed=[]
        class Iterator:
            def __iter__(self):return self
            def __next__(self):return {'type':'inert'}
            def close(self):
                closed.append(True);f.f.f.mono+=11
        class Provider:
            def stream(self,*args):return Iterator()
        f.provider=Provider();adapter=f.make(streaming=True);stream=adapter.stream(f.context,{})
        next(stream)
        from artex.agent.runtime import RunStopped
        with self.assertRaises(RunStopped):stream.close()
        stream.close();self.assertEqual(closed,[True])
