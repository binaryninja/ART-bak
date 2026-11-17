"""Test-only participating writers; never a production authority conformance claim."""
from dataclasses import replace
from threading import Event, Thread
import unittest

from artex.db.agents import AgentMissing, AgentStore
from artex.db.profiles import ProfileNotFound, ProfileStorageError
from owner_writer_reference import (
    AcquisitionProbe, RecordingExecutor, ReferenceCancelled, ReferenceOwner,
    ReferenceSnapshot, ReferenceStale, ReferenceTimeout, ReferenceUncertain,
)


def fixture():
    executor = RecordingExecutor(profiles={1: True, 2: False},
        agents={'analyst': ('Original', None, False), 'builtin': ('Builtin', None, True)})
    return ReferenceOwner(executor), executor


def spawn(callback):
    """Bounded tests always join; daemon is a last-resort failed-test safeguard."""
    outcome = []
    def run():
        try:
            outcome.append((True, callback()))
        except BaseException as error:
            outcome.append((False, error))
    thread = Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


class OwnerWriterReferenceTests(unittest.TestCase):
    def finish(self, job, error=None):
        thread, outcome = job
        thread.join(2)
        self.assertFalse(thread.is_alive(), 'Reference test thread did not finish')
        self.assertEqual(len(outcome), 1)
        if error is None:
            self.assertTrue(outcome[0][0], repr(outcome[0][1]))
        else:
            self.assertFalse(outcome[0][0])
            self.assertIsInstance(outcome[0][1], error)
        return outcome[0][1]

    def mutations(self, executor):
        return [event for event in executor.events if event.kind == 'mutation']

    def test_original_profile_writer_mutations_transaction_and_revision(self):
        owner, executor = fixture()
        old = owner.snapshot()
        self.assertIsNone(owner.set_active(old, 2))
        self.assertEqual(executor.profiles, {1: False, 2: True})
        events = executor.events
        self.assertEqual([event.kind for event in events],
            ['begin', 'execute', 'mutation', 'execute', 'mutation', 'commit'])
        changed = self.mutations(executor)
        self.assertEqual([(event.query.sql, event.query.params, event.depth) for event in changed],
            [(executor.CLEAR_DEFAULT, (), 1), (executor.SET_DEFAULT, (2,), 1)])
        self.assertEqual(owner.revision, 1)
        self.assertFalse(owner.current(old))
        self.assertTrue(owner.current(owner.snapshot()))

    def test_original_metadata_writer_normalization_and_no_added_transaction(self):
        owner, executor = fixture()
        old = owner.snapshot()
        self.assertIsNone(owner.update_metadata(old, 'analyst', '  Renamed  ', ''))
        self.assertEqual(executor.agents['analyst'], ('Renamed', None, False))
        changed = self.mutations(executor)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].query.sql, executor.AGENT_METADATA)
        self.assertEqual(changed[0].query.params, ('analyst', 'Renamed', ''))
        self.assertEqual(changed[0].depth, 0)
        self.assertEqual([event.kind for event in executor.events], ['execute', 'mutation'])
        self.assertFalse(owner.current(old))

    def test_work_guard_excludes_actual_profile_queries_until_release(self):
        owner, executor = fixture()
        old, probe = owner.snapshot(), AcquisitionProbe()
        job = None
        try:
            with owner.guard(old):
                job = spawn(lambda: owner.set_active(old, 2, probe=probe))
                self.assertTrue(probe.contended.wait(1))
                self.assertTrue(probe.attempted.is_set())
                self.assertFalse(probe.acquired.is_set())
                self.assertEqual(executor.events, [])
                self.assertEqual(executor.profiles, {1: True, 2: False})
        finally:
            if job is not None:
                self.finish(job)
        self.assertEqual(len(self.mutations(executor)), 2)
        self.assertFalse(owner.current(old))

    def test_waiting_old_snapshot_rejects_after_first_actual_writer(self):
        owner, executor = fixture()
        old = owner.snapshot()
        entered, release = Event(), Event()
        def before(query):
            if query.sql == executor.AGENT_METADATA:
                entered.set()
                if not release.wait(2):
                    raise AssertionError('Test release was not signaled')
        executor.before_mutation = before
        first = spawn(lambda: owner.update_metadata(old, 'analyst', 'First'))
        second = None
        probe = AcquisitionProbe()
        try:
            self.assertTrue(entered.wait(1))
            second = spawn(lambda: owner.set_active(old, 2, probe=probe))
            self.assertTrue(probe.contended.wait(1))
        finally:
            release.set()
            self.finish(first)
            if second is not None:
                self.finish(second, ReferenceStale)
        self.assertEqual(len(self.mutations(executor)), 1)
        self.assertEqual(executor.profiles, {1: True, 2: False})
        self.assertEqual(owner.revision, 1)
        self.assertFalse(owner.uncertain)

    def test_nested_read_guards_and_body_exception_identity(self):
        owner, executor = fixture()
        snapshot = owner.snapshot()
        marker = ValueError('body failed')
        with self.assertRaises(ValueError) as caught:
            with owner.guard(snapshot):
                with owner.guard(snapshot):
                    self.assertTrue(owner.current(snapshot))
                    raise marker
        self.assertIs(caught.exception, marker)
        self.assertTrue(owner.current(snapshot))
        self.assertEqual(executor.events, [])

    def test_nested_writers_and_explicit_outer_transaction_order(self):
        owner, executor = fixture()
        old = owner.snapshot()
        with owner.write(old) as first:
            self.assertFalse(owner.current(first))
            with executor.transaction() as transaction:
                self.assertIs(transaction, executor)
                owner.set_active(first, 2)
                owner.update_metadata(owner.snapshot(), 'analyst', 'Nested', 'kept')
                self.assertFalse(owner.current(owner.snapshot()))
        controls = [(event.kind, event.depth) for event in executor.events
                    if event.kind in {'begin', 'commit', 'rollback'}]
        self.assertEqual(controls, [('begin', 1), ('begin', 2), ('commit', 2), ('commit', 1)])
        self.assertEqual(owner.revision, 3)
        self.assertTrue(owner.current(owner.snapshot()))
        self.assertEqual(executor.agents['analyst'], ('Nested', 'kept', False))

    def test_original_missing_profile_rolls_back_but_reference_stays_poisoned(self):
        owner, executor = fixture()
        old = owner.snapshot()
        with self.assertRaises(ProfileNotFound):
            owner.set_active(old, 99)
        self.assertEqual(executor.profiles, {1: True, 2: False})
        self.assertEqual(executor.events[-1].kind, 'rollback')
        self.assertTrue(owner.uncertain)
        self.assertEqual(owner.revision, 1)
        self.assertFalse(owner.current(old))
        self.assertFalse(owner.current(owner.snapshot()))
        count = len(executor.events)
        with self.assertRaises(ReferenceUncertain):
            owner.set_active(owner.snapshot(), 2)
        self.assertEqual(len(executor.events), count)

    def test_caught_nested_writer_failure_prevents_outer_success(self):
        owner, executor = fixture()
        with self.assertRaises(ReferenceUncertain):
            with owner.write(owner.snapshot()) as nested:
                try:
                    owner.set_active(nested, 99)
                except ProfileNotFound:
                    pass
        self.assertTrue(owner.uncertain)
        self.assertEqual(owner.revision, 2)
        self.assertEqual(executor.profiles, {1: True, 2: False})

    def test_profile_commit_uncertainty_keeps_rows_and_original_redaction(self):
        owner, executor = fixture()
        executor.commit_error = RuntimeError('synthetic internal commit uncertainty')
        with self.assertRaises(ProfileStorageError):
            owner.set_active(owner.snapshot(), 2)
        self.assertEqual(executor.profiles, {1: False, 2: True})
        self.assertEqual(executor.events[-1].kind, 'commit_uncertain')
        self.assertNotIn('rollback', [event.kind for event in executor.events])
        self.assertTrue(owner.uncertain)

    def test_outer_transaction_commit_exception_is_not_replaced(self):
        owner, executor = fixture()
        marker = RuntimeError('outer commit failed')
        executor.commit_error = marker
        with self.assertRaises(RuntimeError) as caught:
            with owner.write(owner.snapshot()) as fresh:
                with executor.transaction():
                    owner.set_active(fresh, 2)
        self.assertIs(caught.exception, marker)
        self.assertEqual(executor.profiles, {1: False, 2: True})
        self.assertEqual(executor.events[-1].kind, 'commit_uncertain')
        self.assertTrue(owner.uncertain)

    def test_direct_metadata_failure_after_mutation_has_no_invented_rollback(self):
        owner, executor = fixture()
        marker = KeyboardInterrupt('synthetic interrupted result')
        def fail(query):
            raise marker
        executor.after_mutation = fail
        with self.assertRaises(KeyboardInterrupt) as caught:
            owner.update_metadata(owner.snapshot(), 'analyst', 'Changed', 'retained')
        self.assertIs(caught.exception, marker)
        self.assertEqual(executor.agents['analyst'], ('Changed', 'retained', False))
        self.assertEqual([event.kind for event in executor.events], ['execute', 'mutation'])
        self.assertTrue(owner.uncertain)

    def test_cancel_signals_from_other_thread_without_acquiring_work_barrier(self):
        owner, executor = fixture()
        snapshot = owner.snapshot()
        job = None
        try:
            with self.assertRaises(ReferenceCancelled):
                with owner.guard(snapshot):
                    job = spawn(owner.cancel)
                    self.finish(job)
                    self.assertTrue(owner.cancelled)
                    self.assertFalse(owner.current(snapshot))
        finally:
            if job is not None:
                job[0].join(2)
        self.assertEqual(executor.events, [])
        self.assertFalse(owner.uncertain)
        with self.assertRaises(ReferenceCancelled):
            owner.set_active(owner.snapshot(), 2)
        self.assertEqual(owner.revision, 0)

    def test_late_cancel_after_profile_mutation_refuses_success_without_undo(self):
        owner, executor = fixture()
        def cancel_after_set(query):
            if query.sql == executor.SET_DEFAULT:
                owner.cancel()
        executor.after_mutation = cancel_after_set
        with self.assertRaises(ReferenceCancelled):
            owner.set_active(owner.snapshot(), 2)
        self.assertEqual(executor.profiles, {1: False, 2: True})
        self.assertEqual(executor.events[-1].kind, 'commit')
        self.assertTrue(owner.cancelled)
        self.assertTrue(owner.uncertain)

    def test_foreign_and_malformed_snapshot_reject_before_query(self):
        owner, executor = fixture()
        foreign, _ = fixture()
        for snapshot in (foreign.snapshot(), object(), replace(owner.snapshot(), revision=True)):
            with self.subTest(snapshot_type=type(snapshot)), self.assertRaises(ReferenceStale):
                owner.set_active(snapshot, 2)
        self.assertEqual(owner.revision, 0)
        self.assertEqual(executor.events, [])
        self.assertFalse(owner.uncertain)

    def test_timeout_on_observed_contention_does_not_admit_or_mutate(self):
        owner, executor = fixture()
        snapshot, probe = owner.snapshot(), AcquisitionProbe()
        job = None
        try:
            with owner.guard(snapshot):
                job = spawn(lambda: owner.set_active(snapshot, 2, timeout=0, probe=probe))
                self.assertTrue(probe.contended.wait(1))
                self.finish(job, ReferenceTimeout)
                self.assertFalse(probe.acquired.is_set())
                self.assertEqual(executor.events, [])
        finally:
            if job is not None:
                job[0].join(2)
        self.assertEqual(owner.revision, 0)
        self.assertTrue(owner.current(snapshot))

    def test_direct_original_store_bypass_mutates_while_reference_guard_is_held(self):
        owner, executor = fixture()
        snapshot = owner.snapshot()
        job = None
        try:
            with owner.guard(snapshot):
                job = spawn(lambda: AgentStore(executor).update_metadata('analyst', 'Bypass', ''))
                self.finish(job)
                self.assertEqual(executor.agents['analyst'], ('Bypass', None, False))
                self.assertEqual(len(self.mutations(executor)), 1)
                self.assertEqual(owner.revision, 0)
                self.assertTrue(owner.current(snapshot))
        finally:
            if job is not None:
                job[0].join(2)
        # This counterexample is the limit: wrappers do not enforce all writers.
        self.assertTrue(owner.current(snapshot))

    def test_builtin_agent_failure_is_original_and_conservatively_invalidates(self):
        owner, executor = fixture()
        with self.assertRaises(AgentMissing):
            owner.update_metadata(owner.snapshot(), 'builtin', 'Not changed')
        self.assertEqual(executor.agents['builtin'], ('Builtin', None, True))
        self.assertTrue(owner.uncertain)
        self.assertEqual(owner.revision, 1)


if __name__ == '__main__':
    unittest.main()
