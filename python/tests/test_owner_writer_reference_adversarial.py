"""Adversarial reference-protocol tests, not production lifecycle authority.

Only original ProfileStore.set_active and AgentStore.update_metadata mutate the
explicit in-memory executor. Contention is observed via failed nonblocking
acquisition, and assertions inspect original Query mutation events and rows.
No task/run state, application, provider, credentials or live database exists.
"""
from concurrent.futures import Future
import math
from threading import Event, Thread, get_ident
from time import monotonic
import unittest

from artex.db.agents import AgentStore
from artex.db.profiles import ProfileStore
from owner_writer_reference import (
    AcquisitionProbe, RecordingExecutor, ReferenceCancelled, ReferenceOwner,
    ReferenceStale, ReferenceTimeout,
)


class InjectedAbort(BaseException):
    """An exact sentinel proving that original BaseException identity survives."""


class UnresolvedWorkers(AssertionError):
    """Bounded cleanup observed unfinished test work; it did not stop that work."""


class DaemonWorkers:
    """Small test-only worker group with no unbounded shutdown path.

    submit returns a Future; callers must use a bounded result(timeout). close
    joins all threads against one total monotonic budget, then reports any still
    alive. Daemon status prevents a failed test from blocking process exit; it
    does not mean the worker completed or that its effects were undone. Threads
    remain accessible for a deliberately blocked external control to release and
    join afterward. No thread is force-killed and no runtime owner is created.
    """
    def __init__(self, max_workers, *, join_timeout=2.0):
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError('Positive bounded worker count required')
        if type(join_timeout) not in (int, float) or not math.isfinite(join_timeout) or not 0 <= join_timeout <= 5:
            raise ValueError('Finite cleanup budget between zero and five seconds required')
        self.max_workers, self.join_timeout = max_workers, join_timeout
        self._threads = []
        self._closed = False

    @property
    def threads(self):
        return tuple(self._threads)

    def submit(self, callback, *args, **kwargs):
        if self._closed or len(self._threads) >= self.max_workers:
            raise RuntimeError('Worker group closed or submission bound reached')
        future = Future()

        def run():
            if not future.set_running_or_notify_cancel():
                return
            try:
                value = callback(*args, **kwargs)
            except BaseException as error:
                future.set_exception(error)
            else:
                future.set_result(value)

        thread = Thread(target=run, daemon=True, name=f'reference-writer-{len(self._threads) + 1}')
        self._threads.append(thread)
        thread.start()
        return future

    def close(self):
        self._closed = True
        deadline = monotonic() + self.join_timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - monotonic()))
        unresolved = [thread.name for thread in self._threads if thread.is_alive()]
        if unresolved:
            raise UnresolvedWorkers('Unresolved daemon test workers after cleanup budget: ' + ', '.join(unresolved))

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
        return False


class OwnerWriterReferenceAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.original_profiles = {1: True, 2: False, 3: False}
        self.original_agents = {'custom': ('Original', 'before', False)}
        self.db = RecordingExecutor(profiles=dict(self.original_profiles), agents=dict(self.original_agents))
        self.owner = ReferenceOwner(self.db)

    def mutations(self):
        return [event for event in self.db.events if event.kind == 'mutation']

    def assert_no_mutation(self):
        self.assertEqual(self.mutations(), [])
        self.assertEqual(self.db.profiles, self.original_profiles)
        self.assertEqual(self.db.agents, self.original_agents)

    def wait(self, event):
        self.assertTrue(event.wait(1), 'Expected bounded synchronization event')

    def test_contended_writer_cannot_reach_original_mutation_until_guard_releases(self):
        snapshot, probe = self.owner.snapshot(), AcquisitionProbe()
        with DaemonWorkers(max_workers=1) as threads:
            with self.owner.guard(snapshot, timeout=1):
                future = threads.submit(self.owner.update_metadata, snapshot, 'custom', 'Changed',
                                        'description', timeout=2, probe=probe)
                self.wait(probe.attempted)
                self.wait(probe.contended)
                self.assertFalse(probe.acquired.is_set())
                self.assert_no_mutation()
            self.assertIsNone(future.result(2))
        self.assertTrue(probe.acquired.is_set())
        self.assertEqual(self.db.agents['custom'], ('Changed', 'description', False))
        events = self.mutations()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].query.params, ('custom', 'Changed', 'description'))
        self.assertNotEqual(events[0].thread_id, get_ident())
        self.assertFalse(self.owner.current(snapshot))
        self.assertTrue(self.owner.current(self.owner.snapshot()))

    def test_contended_timeout_is_pre_mutation_and_does_not_advance_revision(self):
        snapshot, probe = self.owner.snapshot(), AcquisitionProbe()
        with DaemonWorkers(max_workers=1) as threads:
            with self.owner.guard(snapshot, timeout=1):
                future = threads.submit(self.owner.set_active, snapshot, 2, timeout=0.02, probe=probe)
                self.wait(probe.contended)
                with self.assertRaises(ReferenceTimeout):
                    future.result(1)
                self.assertFalse(probe.acquired.is_set())
                self.assert_no_mutation()
                self.assertEqual(self.owner.snapshot(), snapshot)
        self.assertTrue(self.owner.current(snapshot))

    def test_two_queued_same_snapshot_writers_allow_exactly_one_original_update(self):
        snapshot = self.owner.snapshot()
        probes = [AcquisitionProbe(), AcquisitionProbe()]
        with DaemonWorkers(max_workers=2) as threads:
            with self.owner.guard(snapshot, timeout=1):
                futures = [threads.submit(self.owner.update_metadata, snapshot, 'custom', label,
                           'contender', timeout=2, probe=probe)
                           for label, probe in zip(('Writer A', 'Writer B'), probes)]
                for probe in probes:
                    self.wait(probe.contended)
                    self.assertFalse(probe.acquired.is_set())
                self.assert_no_mutation()
            outcomes = []
            for future in futures:
                try:
                    self.assertIsNone(future.result(2))
                except ReferenceStale:
                    outcomes.append('stale')
                else:
                    outcomes.append('committed')
        self.assertCountEqual(outcomes, ['committed', 'stale'])
        winner = ('Writer A', 'Writer B')[outcomes.index('committed')]
        self.assertEqual(self.db.agents['custom'], (winner, 'contender', False))
        self.assertEqual(len(self.mutations()), 1)
        self.assertEqual(self.mutations()[0].query.params, ('custom', winner, 'contender'))
        self.assertEqual(self.owner.snapshot().revision, snapshot.revision + 1)
        self.assertFalse(self.owner.current(snapshot))
        self.assertTrue(self.owner.current(self.owner.snapshot()))

    def test_foreign_owner_snapshot_cannot_reach_original_store(self):
        other_db = RecordingExecutor(profiles={1: True}, agents={})
        foreign = ReferenceOwner(other_db).snapshot()
        self.assertFalse(self.owner.current(foreign))
        with self.assertRaises(ReferenceStale):
            self.owner.set_active(foreign, 2, timeout=1)
        self.assert_no_mutation()
        self.assertEqual(other_db.events, [])

    def test_old_snapshot_is_rejected_after_success_without_additional_query(self):
        snapshot = self.owner.snapshot()
        self.owner.update_metadata(snapshot, 'custom', 'First', 'accepted', timeout=1)
        before = tuple(self.db.events)
        with self.assertRaises(ReferenceStale):
            self.owner.set_active(snapshot, 2, timeout=1)
        self.assertEqual(tuple(self.db.events), before)
        self.assertEqual(self.db.profiles, self.original_profiles)
        self.assertEqual(self.db.agents['custom'], ('First', 'accepted', False))

    def test_cancel_signal_does_not_wait_for_another_threads_barrier(self):
        snapshot = self.owner.snapshot()
        held, release = Event(), Event()
        probe = AcquisitionProbe()

        def hold():
            with self.owner.guard(snapshot, timeout=1):
                held.set()
                if not release.wait(2):
                    raise AssertionError('Holder release was not signaled')

        with DaemonWorkers(max_workers=3) as threads:
            holder = threads.submit(hold)
            try:
                self.wait(held)
                waiter = threads.submit(self.owner.set_active, snapshot, 2, timeout=2, probe=probe)
                self.wait(probe.contended)
                cancellation = threads.submit(self.owner.cancel)
                cancellation.result(1)
                self.assertFalse(release.is_set())
                self.assert_no_mutation()
            finally:
                release.set()
            with self.assertRaises(ReferenceCancelled):
                holder.result(2)
            with self.assertRaises(ReferenceCancelled):
                waiter.result(2)
        self.assert_no_mutation()
        self.assertFalse(self.owner.current(snapshot))
        self.assertEqual(self.owner.snapshot(), snapshot)
        self.assertFalse(self.owner.uncertain)

    def test_late_cancel_refuses_success_but_does_not_undo_committed_profile_rows(self):
        snapshot = self.owner.snapshot()
        observed_rows = []

        def cancel_after_actual_mutation(query):
            observed_rows.append((query, dict(self.db.profiles)))
            self.owner.cancel()

        self.db.after_mutation = cancel_after_actual_mutation
        with self.assertRaises(ReferenceCancelled):
            self.owner.set_active(snapshot, 2, timeout=1)
        self.assertEqual(len(self.mutations()), 2)
        self.assertEqual(observed_rows[0][1], {1: False, 2: False, 3: False})
        self.assertEqual(observed_rows[1][1], {1: False, 2: True, 3: False})
        self.assertEqual(self.db.profiles, {1: False, 2: True, 3: False})
        self.assertEqual([event.kind for event in self.db.events if event.kind in ('commit', 'rollback')], ['commit'])
        self.assertFalse(self.owner.current(snapshot))
        self.assertFalse(self.owner.current(self.owner.snapshot()))
        self.assertTrue(self.owner.uncertain)

    def test_cancel_between_actual_mutation_and_exception_preserves_original_rollback(self):
        snapshot = self.owner.snapshot()
        mutated, raise_now = Event(), Event()
        abort = InjectedAbort('inert exception after default-clear')

        def stop_after_actual_mutation(query):
            mutated.set()
            if not raise_now.wait(2):
                raise AssertionError('Exception release was not signaled')
            raise abort

        self.db.after_mutation = stop_after_actual_mutation
        with DaemonWorkers(max_workers=2) as threads:
            writer = threads.submit(self.owner.set_active, snapshot, 2, timeout=1)
            try:
                self.wait(mutated)
                self.assertEqual(self.db.profiles, {1: False, 2: False, 3: False})
                cancellation = threads.submit(self.owner.cancel)
                cancellation.result(1)
                self.assertFalse(raise_now.is_set())
            finally:
                raise_now.set()
            with self.assertRaises(InjectedAbort) as raised:
                writer.result(2)
        self.assertIs(raised.exception, abort)
        self.assertEqual(len(self.mutations()), 1)
        self.assertEqual(self.db.profiles, self.original_profiles)
        self.assertEqual([event.kind for event in self.db.events if event.kind in ('commit', 'rollback')], ['rollback'])
        self.assertFalse(self.owner.current(snapshot))
        self.assertFalse(self.owner.current(self.owner.snapshot()))
        self.assertTrue(self.owner.uncertain)

    def test_same_thread_nested_writers_and_original_transaction_are_reentrant(self):
        snapshot = self.owner.snapshot()
        nested_probe = AcquisitionProbe()
        with self.owner.write(snapshot, timeout=1) as first:
            self.assertFalse(self.owner.current(first))
            # The caller explicitly owns this transaction; write() supplies none.
            with self.db.transaction():
                ProfileStore(self.db).set_active(2)
                with self.owner.write(self.owner.snapshot(), timeout=1, probe=nested_probe) as second:
                    AgentStore(self.db).update_metadata('custom', 'Nested', '')
                    self.assertFalse(self.owner.current(second))
                self.assertFalse(self.owner.current(self.owner.snapshot()))
        self.assertTrue(nested_probe.acquired.is_set())
        self.assertFalse(nested_probe.contended.is_set())
        self.assertEqual(self.owner.snapshot().revision, snapshot.revision + 2)
        self.assertTrue(self.owner.current(self.owner.snapshot()))
        self.assertFalse(self.owner.current(first))
        self.assertEqual(self.db.profiles, {1: False, 2: True, 3: False})
        self.assertEqual(self.db.agents['custom'], ('Nested', None, False))
        transactions = [(event.kind, event.depth) for event in self.db.events if event.kind in ('begin', 'commit', 'rollback')]
        self.assertEqual(transactions, [('begin', 1), ('begin', 2), ('commit', 2), ('commit', 1)])
        self.assertEqual([event.depth for event in self.mutations()], [2, 2, 1])
        self.assertTrue(all(event.thread_id == get_ident() for event in self.mutations()))

    def test_direct_original_profile_store_bypasses_reference_guard_and_revision(self):
        snapshot = self.owner.snapshot()
        actual_mutation = Event()
        self.db.after_mutation = lambda query: actual_mutation.set()
        with DaemonWorkers(max_workers=1) as threads:
            with self.owner.guard(snapshot, timeout=1):
                bypass = threads.submit(ProfileStore(self.db).set_active, 2)
                self.wait(actual_mutation)
                self.assertIsNone(bypass.result(1))
                self.assertEqual(self.db.profiles, {1: False, 2: True, 3: False})
                self.assertEqual(self.owner.snapshot(), snapshot)
                self.assertTrue(self.owner.current(snapshot))
                self.assertEqual(len(self.mutations()), 2)
                self.assertTrue(all(event.thread_id != get_ident() for event in self.mutations()))
        # This is the expected coverage counterexample, not a granted writer path.
        self.assertTrue(self.owner.current(snapshot))
        self.assertEqual([event.kind for event in self.db.events if event.kind in ('commit', 'rollback')], ['commit'])


if __name__ == '__main__':
    unittest.main()
