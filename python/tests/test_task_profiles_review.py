"""Independent inert lifecycle boundary regressions for task profile service."""
import unittest
from threading import Thread
import test_task_profiles as fixtures
from test_task_profiles import reads,PROFILE

class Review(unittest.TestCase):
    make=fixtures.TaskProfileTests.make
    call=fixtures.TaskProfileTests.call
    def test_final_idle_callback_revocation_prevents_write(self):
        self.make(reads()+[[PROFILE],[('done',False,False,3)],[(7,)],1,1,[(4,)]])
        count=0
        def idle(binding):
            nonlocal count
            count+=1
            if count==2:self.valid=False
            return True
        self.service.idle=idle
        result=self.call()
        self.assertEqual(result.status,409,result.body)
        self.assertFalse(any(q and q.sql.startswith(('DELETE','INSERT','UPDATE')) for _,q in self.db.calls))
    def test_coordinator_lifetime_change_after_commit_requires_reconciliation(self):
        self.make(reads()+[[PROFILE],[('done',False,False,3)],[(7,)],1,1,[(4,)]])
        def coordinate(binding,change,write):
            receipt=write();self.valid=False;return receipt
        self.service.coordinate=coordinate
        result=self.call()
        self.assertEqual(result.status,503,result.body)
        self.assertTrue(result.json()['committed']);self.assertTrue(result.json()['needs_reconciliation'])

    def test_retained_write_callback_cannot_run_after_guard_exits(self):
        self.make(reads()+[[PROFILE],[('done',False,False,3)],[(7,)],1,1,[(4,)]])
        captured=[]
        self.service.coordinate=lambda binding,change,write:captured.append(write)
        result=self.call();self.assertEqual(result.status,503)
        with self.assertRaises(Exception):captured[0]()
        self.assertFalse(any(q and q.sql.startswith(('DELETE','INSERT','UPDATE')) for _,q in self.db.calls))

    def test_cross_thread_callback_rejected_even_during_active_coordinator(self):
        self.make(reads()+[[PROFILE]])
        errors=[]
        def coordinate(binding,change,write):
            def foreign():
                try:write()
                except Exception as error:errors.append(error)
            thread=Thread(target=foreign);thread.start();thread.join(2)
            self.assertFalse(thread.is_alive())
        self.service.coordinate=coordinate
        result=self.call();self.assertEqual(result.status,503)
        self.assertEqual(len(errors),1)
        self.assertFalse(any(q and q.sql.startswith(('DELETE','INSERT','UPDATE')) for _,q in self.db.calls))

if __name__=='__main__':unittest.main()
