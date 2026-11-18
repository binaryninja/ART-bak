"""Existing-task terminal persistence using real manager ownership and inert rows."""
from unittest.mock import patch
import unittest

from artex.agent.host import AgentHost
from artex.manager import CancellationToken, TaskConflict, TaskManager
from test_agent_runner import make_runner
from test_manager import Repository, Runner


class ManagerCompletionUncertaintyTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.runner = Runner()
        self.manager = TaskManager(self.repository, runner=self.runner)
        self.manager.load_existing()
        self.assertTrue(self.manager.start('1'))
        self.token, self.handle = self.runner.tokens[0], self.runner.handles[0]

    def retained(self):
        self.assertTrue(self.manager.retirement_snapshot('1')[2])
        self.assertFalse(self.manager.owns_generation('1', self.token, self.handle))
        self.assertFalse(self.manager.owns_generation('1', self.token, self.handle, allow_cancelled=True))
        self.assertFalse(self.token.cancelled)
        self.assertFalse(self.manager.start('1'))
        self.assertEqual(self.runner.handles, [self.handle])

    def check_write_error(self, *, persisted):
        original_write = self.repository.set_terminal_status_guarded
        failure = OSError('fixture write error')
        def fail(key, status):
            if persisted:
                original_write(key, status)
            raise failure
        with patch.object(self.repository, 'set_terminal_status_guarded', side_effect=fail) as write:
            with self.assertRaises(OSError) as raised:
                self.manager.complete('1', self.token)
            self.assertIs(raised.exception, failure)
            self.retained()
            self.assertFalse(self.manager.complete('1', self.token))
            write.assert_called_once_with(1, 'done')
        self.assertEqual(self.repository.rows[1]['status'], 'done' if persisted else 'running')

    def test_error_before_write_retains_uncertainty(self):
        self.check_write_error(persisted=False)

    def test_write_then_lost_ack_retains_uncertainty(self):
        self.check_write_error(persisted=True)

    def test_interrupted_reread_retains_owner_and_blocks_replay(self):
        class Interrupted(BaseException):
            pass
        failure = Interrupted()
        with patch.object(self.repository, 'get', side_effect=failure) as read, patch.object(
                self.repository, 'set_terminal_status_guarded',
                wraps=self.repository.set_terminal_status_guarded) as write:
            with self.assertRaises(Interrupted) as raised:
                self.manager.complete('1', self.token)
            self.assertIs(raised.exception, failure)
            self.retained()
            self.assertFalse(self.manager.complete('1', self.token))
            self.assertEqual((write.call_count, read.call_count), (1, 1))
        self.assertEqual(self.repository.rows[1]['status'], 'done')

    def test_missing_after_ack_preserves_conflict_and_retains(self):
        with patch.object(self.repository, 'get', return_value=None):
            with self.assertRaisesRegex(TaskConflict, 'disappeared'):
                self.manager.complete('1', self.token)
        self.retained()

    def test_definite_negative_and_foreign_token_keep_original_contract(self):
        with patch.object(self.repository, 'set_terminal_status_guarded', return_value=False) as write:
            self.assertFalse(self.manager.complete('1', CancellationToken()))
            write.assert_not_called()
            self.assertFalse(self.manager.complete('1', self.token))
            write.assert_called_once_with(1, 'done')
        self.assertTrue(self.manager.owns_generation('1', self.token, self.handle))
        self.assertTrue(self.manager.complete('1', self.token))
        self.assertFalse(self.manager.retirement_snapshot('1')[2])
        self.assertFalse(self.manager.complete('1', self.token))


class RunnerCompletionUncertaintyTests(unittest.TestCase):
    def test_second_host_poll_retains_finished_runner_without_replaying_sql(self):
        repo, runner = Repository(), make_runner()
        manager = TaskManager(repo, runner=runner)
        manager.load_existing()
        host = AgentHost(manager, runner)
        self.assertTrue(host.start('1'))
        handle, = runner.handles()
        self.assertTrue(handle.wait(2))
        original_write = repo.set_terminal_status_guarded
        def lose_ack(key, status):
            self.assertTrue(original_write(key, status))
            raise OSError('injected lost acknowledgement')
        with patch.object(repo, 'set_terminal_status_guarded', side_effect=lose_ack) as write:
            with self.assertRaises(OSError):
                host.poll()
            result = host.poll()
            self.assertFalse(result.all_settled)
            self.assertEqual(result.pending, 1)
            self.assertEqual(runner.handles(), (handle,))
            self.assertFalse(manager.owns_generation('1', handle.token, handle))
            self.assertTrue(manager.retirement_snapshot('1')[2])
            write.assert_called_once_with(1, 'done')
