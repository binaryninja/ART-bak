import unittest
import test_postgres as fixture
from artex.db.postgres import PostgresExecutor
from artex.db.core import Query
class SettingsLockTests(unittest.TestCase):
    def test_negative_control_rowcount_not_dml_success(self):
        conn=fixture.Connection();conn.rowcount=-1;db=PostgresExecutor(conn,cursor_factory=fixture.Cursor)
        with db.transaction() as tx:tx.lock_intercept_settings()
        self.assertIn(('execute','LOCK TABLE settings IN SHARE ROW EXCLUSIVE MODE',()),conn.events)
        self.assertEqual(conn.events[-1],'commit')
        with self.assertRaises(RuntimeError):db.execute(Query('UPDATE settings SET value=$1',('synthetic',)))
        self.assertEqual(conn.events[-1],'rollback')
    def test_no_implicit_transaction_or_arbitrary_command(self):
        conn=fixture.Connection();db=PostgresExecutor(conn,cursor_factory=fixture.Cursor)
        with self.assertRaises(RuntimeError):db.lock_intercept_settings()
        self.assertEqual(conn.events,[])
        with self.assertRaises(TypeError):db.lock_intercept_settings('anything')
    def test_lock_failure_propagates_rollback(self):
        conn=fixture.Connection();db=PostgresExecutor(conn,cursor_factory=fixture.Cursor);conn.execute_error=RuntimeError('synthetic')
        with self.assertRaises(RuntimeError):
            with db.transaction():db.lock_intercept_settings()
        self.assertEqual(conn.events[-1],'rollback')
    def test_config_adapter_requires_lock_capability_before_reads(self):
        from artex.db.tool_intercept_config import ToolInterceptConfigStore
        class Unsupported:
            def fetch_all(self,*a):raise AssertionError('No read before refusal')
        with self.assertRaises(ValueError):ToolInterceptConfigStore(Unsupported())
