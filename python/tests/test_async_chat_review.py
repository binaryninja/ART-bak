"""Independent frontend DTO and dispatcher failure contracts; inert fixtures only."""
import unittest
from threading import Event
from unittest.mock import patch
from artex.db.conversation_activity import Activity
from artex.chat_dispatch import ChatDispatcher,DispatchExecutionError
from test_chat_dispatch import Activities
import test_chat_http as chat_fixture

class Cancelled(BaseException):
    pass

class AsyncChatReviewTests(unittest.TestCase):
    def test_history_rows_use_frontend_seq_and_ts(self):
        fixture=chat_fixture.ChatHTTPTests()
        fixture.setUp()
        fixture.activities.append(Activity(kind='user',summary='first'))
        fixture.activities.append(Activity(kind='result',summary='second'))
        response=fixture.router.dispatch(fixture.request(method='GET',query='?limit=200'))
        self.assertEqual(response.status,200)
        rows=response.json()['items']
        self.assertEqual([row.get('seq') for row in rows],[1,2])
        self.assertTrue(all(isinstance(row.get('ts'),str) for row in rows))
        self.assertNotIn('created_at',rows[0])
        self.assertEqual(response.json()['cursor'],2)

    def test_run_baseexception_settles_receipt_and_retains_uncertain_owner(self):
        dispatcher=ChatDispatcher(single_process=True)
        entered=Event()
        def run(token):
            entered.set()
            raise Cancelled()
        try:
            # Suppress only the test thread's uncaught-error reporting if the
            # regression exists; never execute a real provider or inspect secrets.
            with patch('threading.excepthook',lambda args:None):
                receipt=dispatcher.submit(1,'c','fixture',activities=Activities(),run=run)
                self.assertTrue(entered.wait(1))
                with self.assertRaises(DispatchExecutionError):
                    receipt.future.result(timeout=1)
            self.assertTrue(dispatcher.running(1))
        finally:
            result=dispatcher.close(cancel=True,timeout=1)
            self.assertEqual(result.alive_workers,0)
