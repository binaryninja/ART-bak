import unittest
from threading import Event,Thread
from artex.chat_dispatch import ChatDispatcher,DispatchConflict,DispatchUnavailable,DispatchPersistenceError,DispatchExecutionError
from artex.agent.chat import ChatOutcome,TranscriptTurn
from artex.agent.runtime import RunResult

class Activities:
    def __init__(self,id=1):self.conversation_id=id;self.items=[];self.fail=False;self.enter=None;self.release=None
    def append_many_and_touch(self,items,title_if_default=None):
        return [self.append(item) for item in items]
    def append(self,item):
        if self.enter:self.enter.set();self.release.wait(2)
        if self.fail:raise RuntimeError('secret database failure')
        self.items.append(item);return len(self.items)

def outcome():return ChatOutcome(TranscriptTurn('turn',1,1,[]),RunResult('end_turn',[{'role':'assistant','content':[{'type':'text','text':'hello'}]}],{'input_tokens':2},1,0),True)

class DispatcherTests(unittest.TestCase):
    def make(self,**kwargs):
        obj=ChatDispatcher(single_process=True,**kwargs);self.addCleanup(lambda:obj.close(cancel=True,timeout=2));return obj
    def test_accept_durable_user_and_terminal(self):
        d=self.make();a=Activities();seen=[]
        def run(token):seen.append(len(a.items));return outcome()
        receipt=d.submit(1,'c','hello',activities=a,run=run,worker='assistant')
        self.assertTrue(receipt.future.result(2).persisted);self.assertEqual(seen,[1])
        self.assertEqual([x.kind for x in a.items],['user','result']);self.assertEqual(a.items[-1].input_tokens,2)
        self.assertFalse(d.running(1));self.assertTrue(d.close(timeout=2).drained)
    def test_duplicate_and_changed_identity(self):
        d=self.make();a=Activities();calls=[]
        def run(t):calls.append(1);return outcome()
        first=d.submit(1,'c','hello',activities=a,run=run);first.future.result(2)
        second=d.submit(1,'c','hello',activities=a,run=run)
        self.assertTrue(second.reused);self.assertIs(first.future,second.future);self.assertEqual(calls,[1])
        with self.assertRaises(DispatchConflict):d.submit(1,'c','changed',activities=a,run=run)
    def test_busy_capacity_and_receipt_bounds(self):
        d=self.make(capacity=1,max_receipts=2);entered=Event();release=Event();self.addCleanup(release.set)
        def block(t):entered.set();release.wait(2);return outcome()
        r=d.submit(1,'a','m',activities=Activities(),run=block);self.assertTrue(entered.wait(1))
        with self.assertRaises(DispatchConflict):d.submit(1,'b','m',activities=Activities(),run=block)
        b=d.submit(2,'b','m',activities=Activities(2),run=lambda t:outcome())
        with self.assertRaises(DispatchUnavailable):d.submit(3,'c','m',activities=Activities(3),run=block)
        release.set();r.future.result(2);b.future.result(2)
        with self.assertRaises(DispatchUnavailable):d.submit(3,'c','m',activities=Activities(3),run=block)
    def test_cancel_queued_never_calls_runner(self):
        d=self.make();entered=Event();release=Event();self.addCleanup(release.set)
        def run(t):entered.set();release.wait(2);return outcome()
        first=d.submit(1,'a','m',activities=Activities(),run=run);entered.wait(1)
        calls=[];a=Activities(2)
        second=d.submit(2,'b','m',activities=a,run=lambda t:calls.append(1))
        self.assertTrue(d.cancel(2));release.set();first.future.result(2);self.assertIsNone(second.future.result(2))
        self.assertEqual(calls,[]);self.assertEqual(a.items[-1].summary,'Cancelled before execution')
    def test_cancel_during_user_persist_registered(self):
        d=self.make();a=Activities();a.enter=Event();a.release=Event();self.addCleanup(a.release.set);receipts=[];calls=[]
        thread=Thread(target=lambda:receipts.append(d.submit(1,'c','m',activities=a,run=lambda t:calls.append(1))));thread.start();self.assertTrue(a.enter.wait(1))
        self.assertTrue(d.running(1));self.assertTrue(d.cancel(1))
        with self.assertRaises(DispatchPersistenceError):d.submit(1,'c','m',activities=a,run=lambda t:None)
        a.release.set();thread.join(2);receipts[0].future.result(2);self.assertEqual(calls,[])
    def test_user_persistence_failure_retains_ownership(self):
        d=self.make();a=Activities();a.fail=True
        with self.assertRaises(DispatchPersistenceError) as caught:d.submit(1,'c','m',activities=a,run=lambda t:outcome())
        self.assertNotIn('secret',str(caught.exception));self.assertTrue(d.running(1))
        result=d.close(timeout=2);self.assertFalse(result.drained);self.assertEqual(result.unresolved,1)
    def test_terminal_failure_retained(self):
        d=self.make();a=Activities()
        def run(t):a.fail=True;return outcome()
        r=d.submit(1,'c','m',activities=a,run=run)
        with self.assertRaises(DispatchPersistenceError):r.future.result(2)
        self.assertTrue(d.running(1));self.assertEqual(d.close(timeout=2).unresolved,1)
    def test_invalid_outcome_not_fake_completion(self):
        d=self.make();a=Activities();r=d.submit(1,'c','m',activities=a,run=lambda t:None)
        with self.assertRaises(DispatchExecutionError):r.future.result(2)
        self.assertEqual(a.items[-1].kind,'text');self.assertTrue(a.items[-1].is_error);self.assertTrue(d.running(1))
    def test_close_bounded_and_sticky(self):
        d=self.make();entered=Event();release=Event();self.addCleanup(release.set)
        def run(t):entered.set();release.wait(2);return outcome()
        r=d.submit(1,'c','m',activities=Activities(),run=run);entered.wait(1)
        result=d.close(cancel=True,timeout=0);self.assertFalse(result.drained);self.assertEqual(result.alive_workers,1)
        with self.assertRaises(DispatchUnavailable):d.submit(2,'b','m',activities=Activities(2),run=run)
        release.set();r.future.result(2);self.assertTrue(d.close(timeout=2).drained)
    def test_scope_and_explicit_authority(self):
        with self.assertRaises(ValueError):ChatDispatcher()
        d=self.make()
        with self.assertRaises(ValueError):d.submit(1,'c','m',activities=Activities(2),run=lambda t:outcome())
        self.assertFalse(d.running(1))

    def test_baseexception_callback_does_not_strand_future(self):
        d=self.make();a=Activities()
        def interrupted(token):raise SystemExit('secret')
        receipt=d.submit(1,'c','\n hello',activities=a,run=interrupted)
        with self.assertRaises(DispatchExecutionError):receipt.future.result(2)
        self.assertEqual(d.close(timeout=2).unresolved,1)

    def test_baseexception_admission_is_reported(self):
        d=self.make();a=Activities()
        def interrupted(*args,**kwargs):raise KeyboardInterrupt()
        a.append_many_and_touch=interrupted
        with self.assertRaises(DispatchPersistenceError):d.submit(1,'c','m',activities=a,run=lambda t:outcome())
        self.assertEqual(d.close(timeout=2).unresolved,1)
