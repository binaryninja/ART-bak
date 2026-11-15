"""Inert CLI contracts: all environment, driver, DB, server boundaries patched."""
import contextlib
import io
import unittest
from unittest.mock import patch
import artex.__main__ as cli


class CLITests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.env = self.stack.enter_context(patch.object(cli.os, 'environ', {'ARTEX_PG_DSN': 'dummy-dsn', 'ARTEX_SIGNING_KEY_HEX': 'ab' * 32}))
        self.out, self.err = io.StringIO(), io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.out))
        self.stack.enter_context(contextlib.redirect_stderr(self.err))
        self.driver = self.stack.enter_context(patch.object(cli, '_driver'))
        self.codec = self.stack.enter_context(patch.object(cli, 'BcryptCodec'))
        self.db = self.stack.enter_context(patch.object(cli, 'PostgresExecutor'))
        self.build = self.stack.enter_context(patch.object(cli, 'build_persisted_application'))
        self.server = self.stack.enter_context(patch.object(cli, 'make_server'))

    def untouched(self):
        for mock in (self.driver, self.codec, self.db, self.build, self.server):
            mock.assert_not_called()
        self.db.connect.assert_not_called()

    def test_help_no_configuration_or_dependencies(self):
        self.env.clear()
        with self.assertRaises(SystemExit) as result: cli.main(['--help'])
        self.assertEqual(result.exception.code, 0)
        self.untouched()

    def test_no_implicit_serve(self):
        with self.assertRaises(SystemExit) as result: cli.main([])
        self.assertEqual(result.exception.code, 2)
        self.untouched()

    def test_validation_only(self):
        self.assertEqual(cli.main(['--check-config']), 0)
        self.untouched()
        self.assertIn('were not checked', self.out.getvalue())

    def test_missing_invalid_configuration(self):
        for name, value in [('ARTEX_PG_DSN',''), ('ARTEX_SIGNING_KEY_HEX','short'), ('ARTEX_SIGNING_KEY_HEX','\ud800'), ('ARTEX_PG_DSN','dummy\x00dsn')]:
            with self.subTest(name=name, value=repr(value)), patch.dict(self.env, {name:value}):
                self.assertEqual(cli.main(['--serve']), 2)
        self.untouched()

    def test_invalid_port_or_host_rejected(self):
        for args in (['--serve','--port','0'], ['--serve','--port','65536'], ['--serve','--host','0.0.0.0']):
            with self.assertRaises(SystemExit): cli.main(args)
        self.untouched()

    def test_explicit_serve_only_loopback(self):
        self.assertEqual(cli.main(['--serve', '--port','8099']), 0)
        self.db.connect.assert_called_once_with('dummy-dsn')
        self.build.assert_called_once_with(self.db.connect.return_value.__enter__.return_value, bytes.fromhex('ab' * 32), self.codec.return_value)
        self.server.assert_called_once_with('127.0.0.1', 8099, self.build.return_value, handler_class=cli.QuietHandler)
        self.server.return_value.__enter__.return_value.serve_forever.assert_called_once_with()
        self.db.connect.return_value.__exit__.assert_called_once()
        self.codec.return_value.hash.assert_not_called()
        self.codec.return_value.verify.assert_not_called()

    def test_missing_dependencies_redacted_before_connect(self):
        self.driver.side_effect = RuntimeError('secret dummy-key- dummy-dsn')
        self.assertEqual(cli.main(['--serve']), 2)
        self.db.connect.assert_not_called()
        self.assertNotIn('dummy', self.err.getvalue())
        self.assertIn('psycopg', self.err.getvalue())

    def test_missing_bcrypt_before_connect(self):
        self.codec.return_value._backend.side_effect = ImportError('secret')
        self.assertEqual(cli.main(['--serve']), 2)
        self.db.connect.assert_not_called()

    def test_failures_redacted_and_closed(self):
        self.build.side_effect = RuntimeError('secret dummy-dsn')
        self.assertEqual(cli.main(['--serve']), 1)
        self.db.connect.return_value.__exit__.assert_called_once()
        self.server.assert_not_called()
        self.assertNotIn('secret', self.err.getvalue())

    def test_keyboard_interrupt_closes_resources(self):
        self.server.return_value.__enter__.return_value.serve_forever.side_effect = KeyboardInterrupt()
        self.assertEqual(cli.main(['--serve']), 0)
        self.server.return_value.__exit__.assert_called_once()
        self.db.connect.return_value.__exit__.assert_called_once()

    def test_request_logging_suppressed(self):
        cli.QuietHandler.log_message(None, '%s', 'dummy-secret')
        self.assertEqual(self.err.getvalue(), '')


if __name__ == '__main__': unittest.main()
