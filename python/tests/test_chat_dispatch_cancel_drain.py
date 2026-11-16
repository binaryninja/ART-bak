import unittest
from threading import Event,Thread
from artex.chat_dispatch import ChatDispatcher,DispatchUnavailable,DispatchConflict
from test_chat_dispatch import Activities,outcome

class Tests(unittest.TestCase):
    def fixture(self):
        d=ChatDispatcher(single_process=True)
        entered,release,cb_enter,cb_release=[Event() for _ in range(4)]
        self.addCleanup(lambda:d.close(cancel=True,timeout=2))
        self.addCleanup(release.set);self.addCleanup(cb_release.set)
        def run(token):
            token.on_cancel(lambda:(cb_enter.set(),cb_release.wait(3)))
            entered.set();release.wait(3);return outcome()
        receipt=d.submit(1,'one','hello',activities=Activities(),run=run)
        self.assertTrue(entered.wait(1))
        return d,receipt,release,cb_enter,cb_release
    def test_concurrent_cancel_outlives_completed_worker(self):
        d,r,release,entered,finish=self.fixture()
        t=Thread(target=lambda:d.cancel(1));t.start();self.assertTrue(entered.wait(1))
        release.set();r.future.result(2)
        self.assertTrue(d.running(1))
        with self.assertRaises(DispatchConflict):d.submit(1,'next','new',activities=Activities(),run=lambda t:outcome())
        result=d.close(timeout=.01)
        self.assertFalse(result.drained);self.assertEqual(result.alive_workers,0)
        self.assertTrue(t.is_alive());finish.set();t.join(2)
        self.assertTrue(d.close(timeout=1).drained)
    def test_concurrent_cancel_true_close_is_counted(self):
        d,r,release,entered,finish=self.fixture();results=[]
        t=Thread(target=lambda:results.append(d.close(cancel=True,timeout=.01)));t.start()
        self.assertTrue(entered.wait(1));release.set();r.future.result(2)
        self.assertFalse(d.close(timeout=.01).drained)
        finish.set();t.join(2);self.assertFalse(t.is_alive())
        self.assertTrue(d.close(timeout=1).drained)
    def test_close_waits_for_other_cancellation_callback(self):
        d,r,release,entered,finish=self.fixture()
        t=Thread(target=lambda:d.cancel(1));t.start();self.assertTrue(entered.wait(1))
        release.set();r.future.result(2);results=[]
        closer=Thread(target=lambda:results.append(d.close(timeout=2)));closer.start()
        self.assertFalse(Event().wait(.02) or bool(results))
        finish.set();t.join(2);closer.join(2)
        self.assertTrue(results[0].drained)
    def test_callback_self_close_refuses_before_join(self):
        d=ChatDispatcher(single_process=True);entered=Event();release=Event();observed=[]
        self.addCleanup(lambda:d.close(cancel=True,timeout=2));self.addCleanup(release.set)
        def callback():
            try:d.close(timeout=None)
            except DispatchUnavailable:observed.append('refused')
        def run(token):token.on_cancel(callback);entered.set();release.wait(2);return outcome()
        r=d.submit(1,'one','hello',activities=Activities(),run=run);self.assertTrue(entered.wait(1))
        self.assertTrue(d.cancel(1));self.assertEqual(observed,['refused'])
        release.set();r.future.result(2);self.assertTrue(d.close(timeout=1).drained)
    def test_callback_exception_and_reentrant_cancel_release_accounting(self):
        d=ChatDispatcher(single_process=True);entered=Event();release=Event();seen=[]
        self.addCleanup(lambda:d.close(cancel=True,timeout=2));self.addCleanup(release.set)
        def callback():
            seen.append(d.cancel(1));raise RuntimeError('private callback')
        def run(token):token.on_cancel(callback);entered.set();release.wait(2);return outcome()
        r=d.submit(1,'one','hello',activities=Activities(),run=run);self.assertTrue(entered.wait(1))
        self.assertTrue(d.cancel(1));self.assertEqual(seen,[True]);release.set();r.future.result(2)
        self.assertTrue(d.close(timeout=1).drained)

    def test_completed_callback_releases_conversation_for_new_turn(self):
        d,r,release,entered,finish=self.fixture()
        t=Thread(target=lambda:d.cancel(1));t.start();self.assertTrue(entered.wait(1))
        release.set();r.future.result(2);self.assertTrue(d.running(1))
        finish.set();t.join(2);self.assertFalse(d.running(1))
        new=d.submit(1,'next','new',activities=Activities(),run=lambda t:outcome())
        self.assertTrue(new.future.result(2).persisted)

    def test_direct_issued_token_callback_and_retirement(self):
        d,r,release,entered,finish=self.fixture();token=d._receipts[(1,'one')].token
        t=Thread(target=token.cancel);t.start();self.assertTrue(entered.wait(1))
        release.set();r.future.result(2);self.assertTrue(d.running(1))
        self.assertFalse(d.close(timeout=.01).drained)
        finish.set();t.join(2);self.assertTrue(d.close(timeout=1).drained)
        self.assertTrue(token.cancelled)
        with self.assertRaises(DispatchUnavailable):token.cancel()
        with self.assertRaises(DispatchUnavailable):token.on_cancel(lambda:self.fail('stale callback'))
    def test_immediate_on_cancel_is_owned_and_stale_cannot_affect_new_job(self):
        d=ChatDispatcher(single_process=True);entered=Event();release=Event();tokens=[]
        cb_enter=Event();cb_release=Event()
        self.addCleanup(lambda:d.close(cancel=True,timeout=2));self.addCleanup(release.set);self.addCleanup(cb_release.set)
        def run(token):tokens.append(token);entered.set();release.wait(3);return outcome()
        r=d.submit(1,'one','hello',activities=Activities(),run=run);self.assertTrue(entered.wait(1));token=tokens[0]
        token.cancel()
        t=Thread(target=lambda:token.on_cancel(lambda:(cb_enter.set(),cb_release.wait(3))))
        t.start();self.assertTrue(cb_enter.wait(1));release.set();r.future.result(2);self.assertTrue(d.running(1))
        cb_release.set();t.join(2);self.assertFalse(d.running(1))
        entered.clear();release.clear()
        new=d.submit(1,'two','new',activities=Activities(),run=run);self.assertTrue(entered.wait(1))
        with self.assertRaises(DispatchUnavailable):token.cancel()
        self.assertFalse(tokens[1].cancelled);release.set();new.future.result(2)
    def test_standalone_token_and_other_queue_unchanged(self):
        from artex.chat_dispatch import Cancellation
        token=Cancellation();calls=[];token.cancel();token.on_cancel(lambda:calls.append(1))
        self.assertEqual(calls,[1])
        d,r,release,entered,finish=self.fixture();issued=d._receipts[(1,'one')].token
        t=Thread(target=issued.cancel);t.start();self.assertTrue(entered.wait(1))
        other=ChatDispatcher(single_process=True)
        self.assertTrue(other.close(timeout=1).drained)
        finish.set();release.set();t.join(2);r.future.result(2)
    def test_future_callback_keeps_job_owned_until_return(self):
        d=ChatDispatcher(single_process=True);entered=Event();release=Event();done=Event();finish=Event();observed=[]
        self.addCleanup(lambda:d.close(cancel=True,timeout=2));self.addCleanup(release.set);self.addCleanup(finish.set)
        def run(token):entered.set();release.wait(3);return outcome()
        r=d.submit(1,'one','hello',activities=Activities(),run=run);self.assertTrue(entered.wait(1))
        def completed(future):
            try:d.submit(1,'two','new',activities=Activities(),run=lambda t:outcome())
            except DispatchConflict:observed.append('busy')
            done.set();finish.wait(3)
        r.future.add_done_callback(completed);release.set();self.assertTrue(done.wait(1))
        # Future callbacks execute under the existing settlement condition lock;
        # avoid demanding timeout preemption of that synchronous callback.
        self.assertEqual(observed,['busy']);finish.set()
        self.assertTrue(d.close(timeout=2).drained)
    def test_direct_nested_registration_self_close_refuses(self):
        d=ChatDispatcher(single_process=True);entered=Event();release=Event();tokens=[];seen=[]
        self.addCleanup(lambda:d.close(cancel=True,timeout=2));self.addCleanup(release.set)
        def nested():
            try:d.close(timeout=None)
            except DispatchUnavailable:seen.append('refused')
        def run(token):
            tokens.append(token);token.on_cancel(lambda:token.on_cancel(nested))
            entered.set();release.wait(3);return outcome()
        r=d.submit(1,'one','hello',activities=Activities(),run=run);self.assertTrue(entered.wait(1))
        tokens[0].cancel();self.assertEqual(seen,['refused']);release.set();r.future.result(2)
        self.assertTrue(d.close(timeout=1).drained)
