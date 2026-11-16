import unittest
from threading import Event,Thread
from dataclasses import replace
from artex.task_chat_dispatch import TaskChatDispatcher
from artex.agent.chat import ChatUnavailable
from artex.chat_dispatch import DispatchUnavailable
import test_task_chat as core_fixture

class Tests(unittest.TestCase):
    def make(self):
        f=core_fixture.TaskChatTests();f.setUp();core=f.core()
        dispatcher=TaskChatDispatcher(core,lambda s:None,single_process=True)
        self.addCleanup(lambda:dispatcher.close(cancel=True,timeout=.1))
        return f,core,dispatcher
    def test_direct_core_run_prevents_false_dispatcher_drain(self):
        f,c,d=self.make();entered=Event();release=Event();out=[]
        f.provider.hook=lambda:(entered.set(),release.wait(3))
        def run():
            try:out.append(c.run(1,'direct','hello'))
            except BaseException as e:out.append(e)
        thread=Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(1));result=d.close(cancel=True,timeout=.01)
            self.assertFalse(result.drained);self.assertGreater(result.core_invocations,0);self.assertTrue(c.running(1))
            with self.assertRaises(ChatUnavailable):c.run(1,'new','hello')
            with self.assertRaises(DispatchUnavailable):d.prepare(1)
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertTrue(d.close(timeout=1).drained)
    def test_prepare_callback_lifetime_and_late_materialized_cancel_token(self):
        f,c,d=self.make();entered=Event();release=Event();original=c.provider_resolver;out=[]
        def resolver(*a):entered.set();release.wait(3);return original(*a)
        c.provider_resolver=resolver
        def run():
            try:out.append(c.run(1,'direct','hello'))
            except BaseException as e:out.append(e)
        thread=Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(1));self.assertFalse(d.close(cancel=True,timeout=.01).drained)
        finally:release.set();thread.join(3)
        self.assertEqual(f.provider.calls,[]);self.assertTrue(d.close(timeout=1).drained)
    def test_unresolved_core_persistence_never_cleared_by_close(self):
        f,c,d=self.make();f.provider.hook=lambda:setattr(f,'live',False)
        with self.assertRaises(Exception):c.run(1,'broken','hello')
        result=d.close(cancel=True,timeout=.01);self.assertFalse(result.drained);self.assertEqual(result.unresolved,1)
        self.assertFalse(d.close(timeout=.01).drained)
    def test_outer_submit_callback_retained_and_sealed(self):
        f,c,d=self.make();scope=c.prepare(1);entered=Event();release=Event();errors=[]
        def factory(s):entered.set();release.wait(3);raise RuntimeError('synthetic failure')
        d.activity_store=factory
        def submit():
            try:d.submit(scope,'queued','hello')
            except BaseException as e:errors.append(e)
        thread=Thread(target=submit);thread.start()
        try:
            self.assertTrue(entered.wait(1));result=d.close(timeout=.01)
            self.assertFalse(result.drained);self.assertEqual(result.outer_invocations,1);self.assertGreater(result.alive_workers,0)
            with self.assertRaises(DispatchUnavailable):d.submit(scope,'late','hello')
        finally:release.set();thread.join(3)
        self.assertTrue(d.close(timeout=1).drained)
    def test_self_close_is_not_false_drained(self):
        f,c,d=self.make();observed=[]
        f.provider.hook=lambda:observed.append(d.close(timeout=.01))
        c.run(1,'self','hello')
        self.assertEqual(len(observed),1);self.assertFalse(observed[0].drained)
        self.assertTrue(d.close(timeout=1).drained)
    def test_ticket_single_use_and_no_new_public_run_after_seal(self):
        f,c,d=self.make();scope=c.prepare(1);ticket=c._reserve_queued(scope)
        c.seal()
        from artex.chat_dispatch import Cancellation
        result=c._run_queued(ticket,'accepted','hello',Cancellation());self.assertTrue(result.persisted)
        with self.assertRaises(ChatUnavailable):c._run_queued(ticket,'again','hello',Cancellation())
        self.assertFalse(c.wait(0).drained);c._leave(ticket);self.assertTrue(c.wait(0).drained)
        with self.assertRaises(ChatUnavailable):c.run(1,'other','hello',scope=scope)

    def test_accepted_queued_job_survives_non_cancel_seal(self):
        import test_task_chat_http as fixture
        f=fixture.TaskChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        scope=f.dispatch.prepare(1);sealed=Event();closed=[];original=f.core.seal
        def seal(**kw):
            result=original(**kw);sealed.set();return result
        f.core.seal=seal
        with f.dispatch._queue._condition:
            receipt=f.dispatch.submit(scope,'queued-before-seal','hello')
            thread=Thread(target=lambda:closed.append(f.dispatch.close(cancel=False,timeout=2)));thread.start()
            self.assertTrue(sealed.wait(1))
        thread.join(3);self.assertFalse(thread.is_alive());self.assertTrue(closed[0].drained)
        self.assertTrue(receipt.future.result().persisted);self.assertEqual(len(f.provider.calls),1)
    def test_terminal_projection_retains_ticket_until_actual_completion(self):
        import test_task_chat_http as fixture
        f=fixture.TaskChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        entered=Event();release=Event();original=f.sink.append_many;count=[0]
        def append(items):
            count[0]+=1
            if count[0]==2:entered.set();release.wait(3)
            return original(items)
        f.sink.append_many=append
        receipt=f.dispatch.submit(f.dispatch.prepare(1),'terminal-block','hello')
        try:
            self.assertTrue(entered.wait(1));result=f.dispatch.close(timeout=.01)
            self.assertFalse(result.drained);self.assertGreater(result.core_invocations,0)
        finally:release.set()
        self.assertTrue(f.dispatch.close(timeout=2).drained);self.assertTrue(receipt.future.result().persisted)

    def test_cancellation_callback_retained_after_run_finishes(self):
        from artex.chat_dispatch import Cancellation
        f,c,d=self.make();entered=Event();release=Event();cancel_entered=Event();cancel_release=Event()
        token=Cancellation();token.on_cancel(lambda:(cancel_entered.set(),cancel_release.wait(3)))
        f.provider.hook=lambda:(entered.set(),release.wait(3))
        def run():
            try:c.run(1,'callback','hello',token=token)
            except Exception:pass
        worker=Thread(target=run);worker.start();self.assertTrue(entered.wait(1))
        closer=Thread(target=lambda:d.close(cancel=True,timeout=.1));closer.start()
        try:
            self.assertTrue(cancel_entered.wait(1));release.set();worker.join(2)
            self.assertFalse(worker.is_alive());self.assertFalse(d.close(timeout=.01).drained)
        finally:release.set();cancel_release.set();worker.join(3);closer.join(3)
        self.assertTrue(d.close(timeout=1).drained)

    def test_replaced_public_core_cannot_hide_original(self):
        f,c,d=self.make();d.core=f.core()
        try:
            with self.assertRaises(DispatchUnavailable):d.close(timeout=0)
            with self.assertRaises(DispatchUnavailable):d.prepare(1)
        finally:d.core=c
        self.assertTrue(d.close(timeout=1).drained)

    def test_terminal_projection_core_self_close_does_not_wait(self):
        import test_task_chat_http as fixture
        f=fixture.TaskChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        original=f.sink.append_many;count=[0];observed=[]
        def append(items):
            count[0]+=1
            if count[0]==2:observed.append(f.core.close(timeout=None))
            return original(items)
        f.sink.append_many=append
        receipt=f.dispatch.submit(f.dispatch.prepare(1),'terminal-self','hello')
        self.assertTrue(receipt.future.result(timeout=2).persisted)
        self.assertEqual(len(observed),1);self.assertFalse(observed[0].drained)
        self.assertTrue(f.dispatch.close(timeout=1).drained)

    def test_dispatch_cancel_callback_retained_after_future_settles(self):
        import test_task_chat_http as fixture
        f=fixture.TaskChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        entered=Event();release=Event();cancel_entered=Event();cancel_release=Event()
        f.provider.hook=lambda:(entered.set(),release.wait(3))
        receipt=f.dispatch.submit(f.dispatch.prepare(1),'queue-cancel','hello')
        self.assertTrue(entered.wait(1))
        with f.dispatch._queue._condition:token=f.dispatch._queue._busy[1].token
        token.on_cancel(lambda:(cancel_entered.set(),cancel_release.wait(3)))
        closer=Thread(target=lambda:f.dispatch.cancel(1));closer.start()
        try:
            self.assertTrue(cancel_entered.wait(1));release.set();receipt.future.result(timeout=2)
            result=f.dispatch.close(timeout=.01)
            self.assertFalse(result.drained);self.assertGreater(result.outer_invocations,0)
        finally:release.set();cancel_release.set();closer.join(3)
        self.assertTrue(f.dispatch.close(timeout=1).drained)

    def test_cancelled_before_run_terminal_projection_self_close(self):
        import test_task_chat_http as fixture
        f=fixture.TaskChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        original=f.sink.append_many;count=[0];observed=[]
        def append(items):
            count[0]+=1
            if count[0]==2:observed.append(f.core.close(timeout=None))
            return original(items)
        f.sink.append_many=append
        with f.dispatch._queue._condition:
            receipt=f.dispatch.submit(f.dispatch.prepare(1),'skip-run','hello')
            self.assertTrue(f.dispatch.cancel(1))
        self.assertIsNone(receipt.future.result(timeout=2))
        self.assertEqual(len(observed),1);self.assertFalse(observed[0].drained)
        self.assertEqual(f.provider.calls,[])
        self.assertTrue(f.dispatch.close(timeout=1).drained)

    def test_worker_self_close_during_external_close_does_not_wait(self):
        import test_task_chat_http as fixture
        f=fixture.TaskChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        entered=Event();closing=Event();observed=[];external=[]
        original=f.dispatch._queue.close
        def queue_close(**kw):closing.set();return original(**kw)
        f.dispatch._queue.close=queue_close
        def hook():
            entered.set();self.assertTrue(closing.wait(2))
            observed.append(f.dispatch.close(timeout=None))
        f.provider.hook=hook
        receipt=f.dispatch.submit(f.dispatch.prepare(1),'nested-close','hello')
        self.assertTrue(entered.wait(1))
        thread=Thread(target=lambda:external.append(f.dispatch.close(timeout=2)));thread.start()
        thread.join(3);self.assertFalse(thread.is_alive())
        self.assertTrue(receipt.future.result(timeout=1).persisted)
        self.assertEqual(len(observed),1);self.assertFalse(observed[0].drained)
        self.assertTrue(external[0].drained)
