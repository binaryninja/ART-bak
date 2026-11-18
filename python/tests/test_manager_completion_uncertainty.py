"""Existing-task terminal persistence using real manager ownership and inert rows."""
from unittest.mock import patch
import unittest

from artex.agent.host import AgentHost
from artex.manager import CancellationToken, TaskConflict, TaskManager
from test_agent_runner import make_runner
from test_manager import NOW, Repository, Runner


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

    def test_negative_write_cannot_authorize_a_second_attempt(self):
        with patch.object(self.repository, 'set_terminal_status_guarded', return_value=False) as write:
            self.assertFalse(self.manager.complete('1', CancellationToken()))
            write.assert_not_called()
            self.assertFalse(self.manager.complete('1', self.token))
            self.retained()
            self.assertFalse(self.manager.complete('1', self.token))
            write.assert_called_once_with(1, 'done')

    def test_competing_terminal_writer_supplies_visible_status(self):
        self.repository.rows[1].update(status='timeout', completed_at=NOW)
        self.assertFalse(self.manager.complete('1', self.token))
        self.assertEqual(self.manager.retirement_snapshot('1')[:3], ('timeout', NOW, True))
        self.assertFalse(self.manager.owns_generation('1', self.token, self.handle))

    def test_competing_lifecycle_status_is_visible_even_with_retained_handle(self):
        for status in ('created', 'paused'):
            with self.subTest(status=status):
                repo, runner = Repository(), Runner()
                manager = TaskManager(repo, runner=runner)
                manager.load_existing()
                self.assertTrue(manager.start('1'))
                repo.rows[1]['status'] = status
                with patch.object(repo, 'set_terminal_status_guarded', return_value=False):
                    self.assertFalse(manager.complete('1', runner.tokens[0]))
                self.assertEqual(manager.status('1'), status)
                self.assertTrue(manager.retirement_snapshot('1')[2])

    def test_changed_row_after_ack_is_not_a_completion_receipt(self):
        original_write = self.repository.set_terminal_status_guarded
        def replace_status(key, status):
            self.assertTrue(original_write(key, status))
            self.repository.rows[key]['status'] = 'failed'
            return True
        with patch.object(self.repository, 'set_terminal_status_guarded', side_effect=replace_status):
            self.assertFalse(self.manager.complete('1', self.token))
        self.assertEqual(self.manager.retirement_snapshot('1')[:3], ('failed', NOW, True))
        self.assertFalse(self.manager.complete('1', self.token))

    def test_archive_after_ack_refreshes_local_row_and_blocks_retirement(self):
        original_write = self.repository.set_terminal_status_guarded
        def archive(key, status):
            self.assertTrue(original_write(key, status))
            self.repository.rows[key]['archived_at'] = NOW
            return True
        with patch.object(self.repository, 'set_terminal_status_guarded', side_effect=archive):
            self.assertFalse(self.manager.complete('1', self.token))
        self.assertEqual(self.manager.lifecycle_snapshot('1')['archived_at'], NOW)
        self.assertTrue(self.manager.retirement_snapshot('1')[2])


class RunnerCompletionUncertaintyTests(unittest.TestCase):
    def test_write_acknowledged_but_settlement_callback_raises(self):
        repo, runner = Repository(), make_runner()
        manager = TaskManager(repo, runner=runner)
        manager.load_existing()
        host = AgentHost(manager, runner)
        self.assertTrue(host.start('1'))
        handle, = runner.handles()
        self.assertTrue(handle.wait(2))
        settle = runner.settle_completed
        def fail_after_removing_handle(complete):
            self.assertEqual(settle(complete), ['1'])
            self.assertEqual(runner.handles(), ())
            self.assertEqual(manager.retirement_snapshot('1')[:3], ('done', NOW, True))
            raise OSError('runner acknowledgement lost')
        with patch.object(runner, 'settle_completed', side_effect=fail_after_removing_handle):
            with self.assertRaisesRegex(OSError, 'runner acknowledgement lost'):
                host.poll()
        self.assertEqual(repo.get(1)['status'], 'done')
        self.assertFalse(manager.owns_generation('1', handle.token, handle, allow_cancelled=True))
        self.assertFalse(manager.complete('1', handle.token))
        self.assertEqual(host.poll().pending, 1)
        self.assertFalse(host.drain(0).all_settled)

    def test_cancel_after_uncertain_write_blocks_poll_drain_and_close(self):
        repo, runner = Repository(), make_runner()
        manager = TaskManager(repo, runner=runner)
        manager.load_existing()
        host = AgentHost(manager, runner)
        self.assertTrue(host.start('1'))
        handle, = runner.handles()
        self.assertTrue(handle.wait(2))
        with patch.object(repo, 'set_terminal_status_guarded', side_effect=OSError) as write:
            with self.assertRaises(OSError):
                host.poll()
            handle.token.cancel()
            self.assertTrue(host.stop_admissions())
            state = host.poll()
            self.assertEqual(runner.handles(), ())
            self.assertTrue(manager.retirement_snapshot('1')[2])
            self.assertFalse(state.all_settled)
            self.assertEqual((state.settled, state.running, state.pending), ((), 0, 1))
            drain = host.drain(0)
            self.assertFalse(drain.workers_drained)
            self.assertFalse(drain.all_settled)
            self.assertFalse(host.close_lifetime(drain))
            write.assert_called_once_with(1, 'done')

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
