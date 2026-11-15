import asyncio
import unittest
from artex.failover import (ContextCancelledError, ContextDeadlineExceededError,
                           is_hard_failure, should_failover, status_of, trim_error)


class FailoverTests(unittest.TestCase):
    def test_status_extraction(self):
        for text, expected in [('provider: status 429: quota', 429),
                               ('status 500 then status 400', 500),
                               ('status 5000', 500), ('STATUS 500', 0),
                               ('status ５００', 0), ('status 50', 0),
                               ('status  500', 0), ('connection reset', 0)]:
            with self.subTest(text=text):
                self.assertEqual(status_of(RuntimeError(text)), expected)

    def test_status_classes(self):
        eligible = {401, 402, 403, 404, 408, 429}
        hard = {401, 402, 403, 404}
        for code in range(100, 1000):
            error = RuntimeError(f'provider: status {code}: body')
            self.assertEqual(should_failover(error), code in eligible or code >= 500)
            self.assertEqual(is_hard_failure(error), code in hard)
        self.assertFalse(should_failover(None))
        self.assertTrue(should_failover(ConnectionError('connection reset')))
        self.assertTrue(should_failover(TimeoutError('socket timeout')))

    def test_cancellation(self):
        for error in (ContextCancelledError(), ContextDeadlineExceededError(), asyncio.CancelledError()):
            self.assertFalse(should_failover(error))
            wrapped = RuntimeError('provider: status 503')
            wrapped.__cause__ = error
            self.assertFalse(should_failover(wrapped))
        self.assertFalse(should_failover('status 503', context_cancelled=True))
        self.assertFalse(should_failover('status 503', context_deadline_exceeded=True))
        cyclic = RuntimeError('status 503')
        cyclic.__cause__ = cyclic
        self.assertTrue(should_failover(cyclic))

    def test_trim_error(self):
        self.assertEqual(trim_error(' \n hello\nworld\t '), 'hello world')
        self.assertEqual(trim_error('x' * 300), 'x' * 300)
        self.assertEqual(trim_error('x' * 301), 'x' * 300 + '…')
        self.assertEqual(trim_error('x' * 299 + 'é'), 'x' * 299 + '…')
        self.assertEqual(trim_error('\x1cbody\x1c'), '\x1cbody\x1c')
        self.assertEqual(trim_error('\u0085body\u0085'), 'body')


if __name__ == '__main__':
    unittest.main()
