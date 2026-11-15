"""Independent mutation-boundary regressions using descriptors mocked entirely."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from artex.tools.workspace_mutations import WorkspaceMutations, MutationTarget, WorkspaceMutationUncertain


class Interrupted(BaseException):
    pass


class Review(unittest.TestCase):
    def operations(self, action):
        obj = object.__new__(WorkspaceMutations)
        obj._check_root = lambda: None
        obj._parts = lambda path: [path]
        obj.fs = SimpleNamespace(_directory=lambda parts: nullcontext(42), close=lambda: None)
        target = MutationTarget(action, 'fixture', (1, 2), None,
                                'missing' if action == 'mkdir' else 'file')
        obj._target = lambda *args: target
        return obj, target

    def test_interrupt_after_mutation_attempt_is_uncertain(self):
        for action, name in [('mkdir', 'mkdir'), ('delete', 'unlink')]:
            with self.subTest(action=action):
                obj, target = self.operations(action)
                effects = []
                def syscall(*args, **kwargs):
                    effects.append('mock effect')
                    raise Interrupted('private interrupt')
                with patch('artex.tools.workspace_mutations.os.' + name, syscall):
                    with self.assertRaises(WorkspaceMutationUncertain) as caught:
                        obj.apply(target, SimpleNamespace(check=lambda: None))
                self.assertEqual(effects, ['mock effect'])
                self.assertNotIn('private', str(caught.exception))

    def test_close_error_does_not_hide_mutation_uncertainty(self):
        obj, _ = self.operations('mkdir')
        obj.fs.close = Mock(side_effect=OSError('private close error'))
        with self.assertRaises(WorkspaceMutationUncertain):
            with obj:
                raise WorkspaceMutationUncertain('Reconcile effect')
