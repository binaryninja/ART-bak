"""Independent offline recording review, no provider or database connections."""
import asyncio
import unittest
from contextlib import contextmanager
from artex.llmrec.postgres import PostgresRecorder, RecordingPersistenceError
import test_llmrec_postgres as fixtures

class Review(unittest.TestCase):
    setUp=fixtures.Tests.setUp
    status=fixtures.Tests.status

    def test_post_provider_cancelled_commit_is_uncertain_no_retry(self):
        @contextmanager
        def interrupted():
            yield self.db
            raise asyncio.CancelledError('private commit detail')
        self.db.transaction=interrupted
        try:
            self.rec.complete({}, {})
        except BaseException as error:
            self.assertIsInstance(error,RecordingPersistenceError)
            self.assertNotIn('private',str(error))
        else:self.fail('Uncertain commit reported success')
        self.assertEqual(self.provider.calls,1)

    def test_close_failure_still_records_partial_usage_and_error(self):
        class BrokenClose:
            def __iter__(self):return self
            def __next__(self):return {'type':'message_start','usage':{'input_tokens':2}}
            def close(self):raise RuntimeError('private close detail')
        self.provider.stream=lambda *args:BrokenClose()
        stream=self.rec.stream({},{});next(stream)
        with self.assertRaises(RuntimeError):stream.close()
        self.assertEqual(self.status(),'error')
        self.assertEqual(self.db.rows[-2].params[6],2)
        self.assertNotIn('private close',repr(self.db.rows))

    def test_error_after_stop_does_not_claim_success(self):
        def events(*args):
            yield {'type':'message_stop'}
            raise RuntimeError('private failure after terminal')
        self.provider.stream=events
        with self.assertRaises(RuntimeError):list(self.rec.stream({},{}))
        self.assertEqual(self.status(),'error')

    def test_provider_baseexception_still_settles_redacted_error(self):
        def abort(*args):raise asyncio.CancelledError('private provider detail')
        self.provider.complete=abort
        with self.assertRaises(asyncio.CancelledError):self.rec.complete({}, {})
        self.assertEqual(self.status(),'error')
        self.assertNotIn('private provider',repr(self.db.rows))
