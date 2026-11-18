"""Host pair identity and slot unwind on empty managers with inert callbacks."""
import unittest

from artex.agent.host import AgentHost
from artex.manager import RuntimeUnavailable, TaskManager


class _InertRunner:
    def _unused(self, *args, **kwargs):
        raise AssertionError('No task lifecycle method may run in this fixture')

    validate_create = ready = start = handles = settle_completed = _unused


class _ClaimRunner(_InertRunner):
    def __init__(self):
        self.claims = 0
        self.on_claim = None

    def claim_host(self, host):
        self.claims += 1
        if self.on_claim is not None:
            self.on_claim(host)


def _manager(runner):
    return TaskManager(object(), runner=runner)


class HostOriginalBindingsTests(unittest.TestCase):
    def test_replacement_pair_refused_before_claim_or_work(self):
        runner, replacement = _ClaimRunner(), _ClaimRunner()
        manager = _manager(runner)
        host = AgentHost(manager, runner)
        host.manager, host.runner = _manager(replacement), replacement
        with self.assertRaises(RuntimeUnavailable):
            host.advance_owned(lambda: self.fail('substituted pair accepted'))
        self.assertEqual(replacement.claims, 0)
        self.assertEqual(runner.claims, 1)

    def test_replacement_from_claim_refuses_work_and_unwinds_reserved_slot(self):
        runner, replacement = _ClaimRunner(), _InertRunner()
        manager = _manager(runner)
        host = AgentHost(manager, runner)
        def substitute_in_reserved_claim(host):
            if host.owns_work(runner):
                host.manager, host.runner = _manager(replacement), replacement
        runner.on_claim = substitute_in_reserved_claim
        with self.assertRaises(RuntimeUnavailable):
            host.advance_owned(lambda: self.fail('claim substitution accepted'))
        host.manager, host.runner = manager, runner
        runner.on_claim = None
        self.assertFalse(host.owns_work(runner))
        result = host.advance_owned(lambda: host.owns_work(runner))
        self.assertFalse(result.busy)
        self.assertIs(result.value, True)

    def test_claim_descriptor_replacement_refused_before_claim_invocation(self):
        class DescriptorRunner(_InertRunner):
            on_lookup = None

            @property
            def claim_host(self):
                if self.on_lookup is not None:
                    self.on_lookup()
                return claims.append

        claims = []
        runner, replacement = DescriptorRunner(), _InertRunner()
        manager = _manager(runner)
        host = AgentHost(manager, runner)
        self.assertEqual(claims, [host])
        def replace_pair():
            host.manager, host.runner = _manager(replacement), replacement
        runner.on_lookup = replace_pair
        with self.assertRaises(RuntimeUnavailable):
            host.advance_owned(lambda: self.fail('work after descriptor substitution'))
        self.assertEqual(claims, [host])

    def test_pair_change_inside_inert_work_prevents_success(self):
        runner, replacement = _InertRunner(), _InertRunner()
        manager = _manager(runner)
        host = AgentHost(manager, runner)
        def change():
            host.manager, host.runner = _manager(replacement), replacement
            return 'unverified'
        with self.assertRaises(RuntimeUnavailable):
            host.advance_owned(change)
        host.manager, host.runner = manager, runner
        self.assertFalse(host.advance_owned(lambda: None).busy)

    def test_cancelled_work_unwinds_for_original_runner_without_claim(self):
        class Cancel(BaseException):
            pass
        runner = _InertRunner()
        host = AgentHost(_manager(runner), runner)
        cancelled = Cancel()
        def cancel():
            self.assertTrue(host.owns_work(runner))
            raise cancelled
        with self.assertRaises(Cancel) as raised:
            host.advance_owned(cancel)
        self.assertIs(raised.exception, cancelled)
        self.assertFalse(host.owns_work(runner))
        self.assertFalse(host.advance_owned(lambda: None).busy)
