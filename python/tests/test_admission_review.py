"""Independent shared-capacity quarantine and callback-boundary tests."""
import unittest
import test_admission as fixtures
from artex.manager import RuntimeUnavailable

class Interrupted(BaseException):pass

class Review(unittest.TestCase):
    setUp=fixtures.Tests.setUp
    def test_mutation_baseexception_reconciles_without_losing_reservation(self):
        with self.assertRaises(Interrupted):
            with self.capacity.admission_guard('start','5'):raise Interrupted()
        self.assertFalse(self.capacity.poll().busy)
        self.facade.control_task('5','resume')
        self.assertEqual(len(self.runner.handles()),1)
    def test_postcall_baseexception_quarantines_and_unsets_busy(self):
        original=self.host.poll;calls=[]
        def interrupted():
            calls.append(1)
            if len(calls)==2:raise Interrupted()
            return original()
        self.host.poll=interrupted
        with self.assertRaises(Interrupted):
            with self.capacity.admission_guard('start','5'):pass
        self.host.poll=original
        self.assertTrue(self.capacity.poll().busy)
        self.assertFalse(self.capacity.reconcile().busy)
    def test_host_callback_reentrant_admission_fails_without_deadlock(self):
        original=self.host.poll;attempts=[]
        def reentrant():
            with self.assertRaises(RuntimeUnavailable):
                with self.capacity.admission_guard('start','5'):self.fail('reentered')
            attempts.append(True)
            return original()
        self.host.poll=reentrant
        with self.capacity.admission_guard('start','5'):pass
        self.assertEqual(len(attempts),2)
    def test_host_rebind_during_poll_does_not_authorize_mutation(self):
        original=self.host.poll
        def change():
            result=original();self.host.manager=object();return result
        self.host.poll=change
        with self.assertRaises(RuntimeUnavailable):
            with self.capacity.admission_guard('start','5'):self.fail('admitted after binding changed')
        self.assertEqual(self.runner.handles(),())

if __name__=='__main__':unittest.main()
