"""Negative ownership checks against a normally constructed empty task manager."""
from unittest import TestCase
from unittest.mock import patch

from artex.agent.bootstrap import BootstrapConflict, BootstrapCoordinator, BootstrapJournal
from artex.agent.bootstrap_handle import BootstrapHandle
from artex.db.tasks import TaskStore
from artex.manager import CancellationToken, TaskManager
from artex.sidequestion import Model


def unexpected(*args, **kwargs):
    raise AssertionError('Unexpected callback during inert ownership check')


class InertExecutor:
    transaction = staticmethod(unexpected)


class InheritedManager(TaskManager):
    pass


class BootstrapOriginalPredicateTests(TestCase):
    def setUp(self):
        self.executor = InertExecutor()
        self.store = TaskStore(self.executor)
        self.manager = TaskManager(self.store)
        self.coordinator = BootstrapCoordinator(BootstrapJournal(self.executor),
                                               planner_factory=unexpected,
                                               lifecycle_guard=unexpected, current=unexpected)

    def make(self, manager):
        return BootstrapHandle({'id': 1, 'exploration_id': 2}, CancellationToken(),
                               coordinator=self.coordinator, manager=manager,
                               model=Model(identity='fixture'), system='fixture',
                               policy_revision='fixture', request_id='fixture',
                               engine_factory=unexpected)

    def test_original_and_inherited_predicate_deny_uninstalled_handle(self):
        for manager in (self.manager, InheritedManager(self.store)):
            handle = self.make(manager)
            handle._binding()
            for cancelled in (False, True):
                with self.subTest(manager=type(manager), cancelled=cancelled):
                    self.assertFalse(manager.owns_generation('1', handle.token, handle,
                                                             allow_cancelled=cancelled))
                    with self.assertRaisesRegex(BootstrapConflict, 'not installed'):
                        handle._owned(allow_cancelled=cancelled)

    def test_instance_replacement_after_construction_rejected_before_dispatch(self):
        handle = self.make(self.manager)
        self.manager.owns_generation = unexpected
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), self.assertRaises(BootstrapConflict):
                handle._owned(allow_cancelled=cancelled)
        with self.assertRaises(BootstrapConflict):
            handle._binding()

    def test_constructor_refuses_subclass_override_and_foreign_bound_original(self):
        class Override(TaskManager):
            owns_generation = unexpected
        other = TaskManager(self.store)
        self.manager.owns_generation = other.owns_generation
        for manager in (Override(self.store), self.manager):
            with self.subTest(manager=type(manager)), self.assertRaises(ValueError):
                self.make(manager)

    def test_descriptor_change_after_construction_never_runs_replacement_code(self):
        handle = self.make(self.manager)
        with patch.object(TaskManager, 'owns_generation', property(unexpected)):
            with self.assertRaises(BootstrapConflict):
                handle._owned()
            with self.assertRaises(ValueError):
                self.make(self.manager)
        with self.assertRaisesRegex(BootstrapConflict, 'not installed'):
            handle._owned()
