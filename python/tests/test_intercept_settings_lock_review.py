"""Independent fixed-control SQL ownership and unchanged DML strictness."""
import unittest
from threading import Event,Thread
import test_postgres as fixture
from artex.db.postgres import PostgresExecutor
from artex.db.core import Query

class SettingsLockReview(unittest.TestCase):
    def test_control_never_consults_rowcount(self):
        class Cursor(fixture.Cursor):
            @property
            def rowcount(self):raise AssertionError('control read rowcount')
        conn=fixture.Connection();db=PostgresExecutor(conn,cursor_factory=Cursor)
        with db.transaction():db.lock_intercept_settings()
        self.assertEqual([e for e in conn.events if isinstance(e,tuple)],[('execute','LOCK TABLE settings IN SHARE ROW EXCLUSIVE MODE',())])
    def test_other_thread_cannot_borrow_active_transaction(self):
        conn=fixture.Connection();db=PostgresExecutor(conn,cursor_factory=fixture.Cursor)
        started=Event();finished=Event();errors=[]
        def lock():
            started.set()
            try:db.lock_intercept_settings()
            except RuntimeError as error:errors.append(error)
            finally:finished.set()
        with db.transaction():
            t=Thread(target=lock);t.start();self.assertTrue(started.wait(1));self.assertFalse(finished.is_set())
        t.join(2);self.assertFalse(t.is_alive());self.assertEqual(len(errors),1)
        self.assertFalse(any(isinstance(e,tuple) for e in conn.events))
    def test_closed_and_negative_dml_remain_refused(self):
        conn=fixture.Connection();db=PostgresExecutor(conn,cursor_factory=fixture.Cursor)
        conn.rowcount=-1
        with self.assertRaises(RuntimeError):db.execute(Query('DELETE FROM settings'))
        self.assertEqual(conn.events[-1],'rollback')
        db.close();before=list(conn.events)
        with self.assertRaises(RuntimeError):db.lock_intercept_settings()
        self.assertEqual(conn.events,before)
