"""Independent synthetic disclosure boundaries for main-session reads."""
import unittest
from contextlib import contextmanager
from test_main_sessions import Tests,NOW
class Review(unittest.TestCase):
    def test_transaction_exit_revocation_hides_result(self):
        f=Tests();f.make([[(5,)],[(1,NOW)]]);original=f.db.transaction
        @contextmanager
        def tx():
            with original() as db:yield db
            f.request_live[0]=False
        f.db.transaction=tx
        self.assertEqual(f.call().status,403)
    def test_request_callback_replaced_after_fetch_denied(self):
        f=Tests();f.make([[(5,)],[(1,NOW)]]);original=f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'main_sessions' in q.sql:f.service.authorize=lambda *a:True
            return rows
        f.db.fetch_all=fetch
        self.assertEqual(f.call().status,403)
