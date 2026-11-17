"""Actual same-process owner/host/engine; inert SQL/provider, real JSONL."""
import json
import threading
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_task_continuation as fixture
from artex.agent.bootstrap import BootstrapUnresolved
from artex.agent.engine import TaskEngineHandle
from artex.agent.task_continuation import TaskContinuationOwner
from artex.providers import Response


class Tests(unittest.TestCase):
    def setUp(self):
        self.f = self.make()
        self.requests = []
        self.install_provider(self.f, self.requests)

    def make(self):
        f = fixture.Tests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        return f

    def install_provider(self, f, requests):
        def transport(config):
            def invoke(context, url, headers, body, stream):
                requests.append(json.loads(body))
                return Response(200, json.dumps({'choices': [{'message': {
                    'role': 'assistant', 'content': 'inert planner completed'}, 'finish_reason': 'stop'}]}))
            return invoke
        f.f.p.factory._transport_factory = transport
        f.f.p.factory.invalidate()

    def install(self, f=None):
        f = f or self.f
        f.app.host.install_continuation(f.owner)

    def pause_resume(self, f=None):
        f = f or self.f
        old = f.app.runner.handles()[0]
        f.app.host.pause('5', timeout=0)
        f.app.host.resume_suspended('5')
        new = f.app.runner.handles()[0]
        result = f.app.host.advance_owned(lambda: f.app.runner.advance('5', new.token, 1.))
        self.assertEqual(result.value.action, 'bootstrap_prepared')
        self.assertIsNot(new, old)
        return old, new

    def test_pending_notifications_order_payload_and_dedupe_survive_private_continuation(self):
        self.install()
        old = self.f.handle
        self.assertTrue(old._child.notify('hint-1', 'hint', 0, 'preserve first'))
        self.assertTrue(old._child.notify('hint-2', 'manual', 3, 'preserve second'))
        original = deepcopy(old._child._events)
        _, new = self.pause_resume()
        self.assertEqual(new._child._events, original)
        self.assertEqual(tuple(new._child._pending_notifications), ('hint-1', 'hint-2'))
        self.assertIsNot(new._child._events[0], old._child._events[0])
        self.assertEqual(old._child._events, original)
        self.assertFalse(new._child.notify('hint-1', 'hint', 0, 'duplicate'))
        self.assertFalse(old._child.notify('late', 'hint', 0, 'closed old generation'))
        self.assertEqual(self.requests, [])
        self.assertIs(new._receipt, old._receipt)
        self.assertTrue(old.token.cancelled)
        self.assertFalse(new.token.cancelled)
        self.assertFalse(self.f.app.manager.owns_generation('5', old.token, old))
        self.assertTrue(self.f.app.manager.owns_generation('5', new.token, new))

    def test_actual_next_planner_receives_notifications_once_and_consumed_ids_survive_again(self):
        self.install()
        self.f.f.f.f.seed()  # otherwise a fresh graph could wake without a hint
        self.f.handle._child.notify('operator', 'hint', 0, 'unique pending instruction')
        _, new = self.pause_resume()
        result = self.f.app.host.advance_owned(lambda: self.f.app.runner.advance('5', new.token, 2.))
        self.assertEqual(result.value.action, 'planner')
        self.assertEqual(len(self.requests), 1)
        request = json.dumps(self.requests[0])
        self.assertEqual(request.count('unique pending instruction'), 1)
        self.assertFalse(new._child._pending_notifications)
        self.assertFalse(new._child.notify('operator', 'hint', 0, 'replay consumed instruction'))
        _, newest = self.pause_resume()
        self.assertFalse(newest._child._events)
        self.assertFalse(newest._child.notify('operator', 'hint', 0, 'second replay'))
        self.assertEqual(len(self.requests), 1)

    def test_notification_accepted_during_planner_prepare_remains_pending(self):
        self.install()
        engine = self.f.handle._child
        engine.notify('first', 'hint', 0, 'already selected')
        entered, release = threading.Event(), threading.Event()
        original = engine.planner.prepare
        errors = []

        def prepare(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('test release timed out')
            return original(*args, **kwargs)

        def advance():
            try:
                self.f.app.host.advance_owned(lambda: self.f.app.runner.advance('5', self.f.handle.token, 1.))
            except BaseException as error:
                errors.append(error)

        with patch.object(engine.planner, 'prepare', prepare):
            thread = threading.Thread(target=advance)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertTrue(engine.notify('late', 'hint', 0, 'not selected yet'))
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(tuple(engine._pending_notifications), ('late',))
        _, new = self.pause_resume()
        self.assertEqual(tuple(new._child._pending_notifications), ('late',))
        self.assertFalse(new._child.notify('first', 'hint', 0, 'consumed duplicate'))
        self.assertFalse(new._child.notify('late', 'hint', 0, 'queued duplicate'))

    def test_notify_racing_pause_close_is_either_retained_or_refused(self):
        for after_close in (False, True):
            with self.subTest(after_close=after_close):
                f = self.make()
                self.install(f)
                old = f.handle
                original = old._child.cancel
                entered, release = threading.Event(), threading.Event()
                errors = []

                def cancel(reason):
                    if after_close:
                        original(reason)
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError('test release timed out')
                    if not after_close:
                        original(reason)

                def pause():
                    try:
                        f.app.host.pause('5', timeout=2)
                    except BaseException as error:
                        errors.append(error)

                with patch.object(old._child, 'cancel', cancel):
                    thread = threading.Thread(target=pause)
                    thread.start()
                    try:
                        self.assertTrue(entered.wait(3))
                        accepted = old._child.notify('race', 'hint', 0, 'must not disappear')
                    finally:
                        release.set()
                        thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertFalse(errors)
                self.assertEqual(accepted, not after_close)
                f.app.host.resume_suspended('5')
                new = f.app.runner.handles()[0]
                f.app.host.advance_owned(lambda: f.app.runner.advance('5', new.token, 1.))
                self.assertEqual('race' in new._child._pending_notifications, accepted)

    def test_concurrent_duplicate_notifications_have_one_owner_across_resume(self):
        self.install()
        engine = self.f.handle._child
        barrier = threading.Barrier(8)
        accepted = []

        def notify():
            barrier.wait(3)
            accepted.append(engine.notify('same-id', 'hint', 0, 'one notification'))

        threads = [threading.Thread(target=notify) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(accepted), 1)
        _, new = self.pause_resume()
        self.assertEqual(len(new._child._events), 1)
        self.assertFalse(new._child.notify('same-id', 'hint', 0, 'duplicate after resume'))

    def test_paused_payload_mutation_refuses_resume_before_sql_write(self):
        self.install()
        old = self.f.handle
        old._child.notify('hint', 'hint', 0, 'approved pending text')
        self.f.app.host.pause('5', timeout=0)
        old._child._pending_notifications['hint']['detail'] = 'changed after pause'
        updates = []
        original = self.f.db.fetch_all

        def fetch(query):
            if query.sql.startswith('UPDATE tasks SET paused=false'):
                updates.append(query)
            return original(query)

        self.f.db.fetch_all = fetch
        with self.assertRaises(Exception):
            self.f.app.host.resume_suspended('5')
        self.assertFalse(updates)
        self.assertTrue(self.f.owner.retained(old))
        self.assertFalse(self.requests)

    def test_replacement_capacity_or_existing_notification_never_silently_overwrites(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                f = self.make()
                self.install(f)
                old = f.handle
                old._child.notify('first', 'hint', 0, 'first')
                old._child.notify('second', 'hint', 0, 'second')
                f.app.host.pause('5', timeout=0)
                f.app.host.resume_suspended('5')
                new = f.app.runner.handles()[0]
                original = TaskEngineHandle.__init__

                def initialize(engine, *args, **kwargs):
                    original(engine, *args, **kwargs)
                    if existing:
                        engine.notify('unexpected', 'hint', 0, 'do not overwrite')
                    else:
                        engine.max_events = 1

                with patch.object(TaskEngineHandle, '__init__', initialize):
                    with self.assertRaises(BootstrapUnresolved):
                        f.app.host.advance_owned(lambda: f.app.runner.advance('5', new.token, 1.))
                self.assertTrue(f.owner.retained(old))
                self.assertTrue(f.owner.retained(new))
                self.assertTrue(new.unresolved)
                if existing:
                    self.assertEqual(new._child._pending_notifications['unexpected']['detail'], 'do not overwrite')

    def test_final_resume_grant_revocation_after_copy_retains_both_generations(self):
        f = self.f
        old = f.handle

        def grant(proposal):
            handles = f.app.runner.handles()
            if proposal.action == 'resume' and handles:
                current = handles[0]
                if current is not old and current._child is not None and current._child._pending_notifications:
                    return False
            return True

        f.owner = TaskContinuationOwner(f.app, authorize=grant)
        self.install()
        old._child.notify('hint', 'hint', 0, 'retained on denied transfer')
        f.app.host.pause('5', timeout=0)
        f.app.host.resume_suspended('5')
        new = f.app.runner.handles()[0]
        with self.assertRaises(BootstrapUnresolved):
            f.app.host.advance_owned(lambda: f.app.runner.advance('5', new.token, 1.))
        self.assertTrue(f.owner.retained(old))
        self.assertTrue(f.owner.retained(new))
        self.assertTrue(new.unresolved)
        self.assertFalse(self.requests)
        self.assertEqual(old._child._events[0]['detail'], 'retained on denied transfer')

    def resume_without_advance(self, f):
        self.install(f)
        f.handle._child.notify('hint', 'hint', 0, 'original retained queue')
        f.app.host.pause('5', timeout=0)
        f.app.host.resume_suspended('5')
        return f.app.runner.handles()[0]

    def assert_retained_failure(self, f, new):
        owner, cap, _ = new._continuation
        self.assertEqual(owner._records[cap]['state'], 'quarantined')
        self.assertTrue(owner._records[cap]['failed'])
        self.assertTrue(owner.retained(f.handle))
        self.assertTrue(owner.retained(new))
        self.assertTrue(new.unresolved)
        self.assertEqual(f.handle._child._events[0]['detail'], 'original retained queue')
        self.assertFalse(self.requests)

    def test_factory_and_each_post_transfer_binding_failure_retain_both(self):
        for boundary in ('__init__', 'bind_cleanup', 'bind_timing', 'bind_process'):
            for error in (RuntimeError, KeyboardInterrupt):
                with self.subTest(boundary=boundary, error=error):
                    f = self.make()
                    new = self.resume_without_advance(f)
                    with patch.object(TaskEngineHandle, boundary, side_effect=error('inert boundary failure')):
                        with self.assertRaises(BootstrapUnresolved):
                            f.app.host.advance_owned(lambda: f.app.runner.advance('5', new.token, 1.))
                    self.assert_retained_failure(f, new)
                    if boundary != '__init__':
                        self.assertEqual(new._child._events, f.handle._child._events)

    def test_concurrent_other_task_pause_reservation_cannot_prune_activating_record(self):
        # Hydrate a second installed original handle in this synthetic registry.
        # Then run the actual pause-reservation/pruning path while task 5's
        # actual resumed factory is blocked. No owner method is replaced.
        from dataclasses import replace
        from artex.agent.bootstrap_handle import BootstrapHandle
        from artex.agent.engine import EngineScope
        from artex.manager import CancellationToken, _Run
        f = self.f
        old = f.handle
        profile_task = deepcopy(f.f.p.task); profile_task.id = 6
        original_get = f.f.p.tasks.get
        f.f.p.tasks.get = lambda task_id: profile_task if int(task_id) == 6 else original_get(task_id)
        row = deepcopy(f.app.manager._tasks['5']); row['id'] = '6'
        second = BootstrapHandle(row, CancellationToken(), coordinator=old.coordinator,
            manager=old.manager, model=old.model, system=old.system,
            policy_revision=old.policy_revision, request_id=old.request_id, engine_factory=old._factory)
        second._receipt = replace(old._receipt, task_id=6)
        second._phase = 'engine'
        second._child = old._factory(row, second.token,
            EngineScope(6, 9, old._receipt.run_id, old.policy_revision), lambda *a: False, old._child.guard)
        run = _Run(token=second.token, handle=second, started=True); run.ready.set()
        f.app.manager._tasks['6'] = row; f.app.manager._runs['6'] = run
        new = self.resume_without_advance(f)
        owner, cap, _ = new._continuation
        entered, release = threading.Event(), threading.Event()
        original = TaskEngineHandle.__init__
        errors = []

        def initialize(engine, *args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('test release timed out')
            original(engine, *args, **kwargs)

        def advance():
            try:
                f.app.host.advance_owned(lambda: f.app.runner.advance('5', new.token, 1.))
            except BaseException as error:
                errors.append(error)

        with patch.object(TaskEngineHandle, '__init__', initialize):
            thread = threading.Thread(target=advance); thread.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(owner._records[cap]['state'], 'activating')
                other = f.app.host._invoke(lambda: owner.reserve_pause(second), admission=False)
                self.assertIn(other, owner._records)
                self.assertIn(cap, owner._records)
                self.assertTrue(owner.retained(old)); self.assertTrue(owner.retained(new))
            finally:
                release.set(); thread.join(3)
        self.assertFalse(thread.is_alive()); self.assertFalse(errors)
        self.assertEqual(new._child._events, old._child._events)
        self.assertEqual(owner._records[cap]['state'], 'consumed')
        self.assertFalse(owner.retained(old))

    def test_final_binding_revocation_or_seal_retains_transferred_queue(self):
        for seal in (False, True):
            with self.subTest(seal=seal):
                f = self.make(); new = self.resume_without_advance(f)
                original = TaskEngineHandle.bind_process
                def bind(engine, *args):
                    original(engine, *args)
                    if seal:
                        f.owner.seal_resume()
                    else:
                        f.allowed = False
                with patch.object(TaskEngineHandle, 'bind_process', bind):
                    with self.assertRaises(BootstrapUnresolved):
                        f.app.host.advance_owned(lambda: f.app.runner.advance('5', new.token, 1.))
                self.assert_retained_failure(f, new)
                self.assertEqual(new._child._events, f.handle._child._events)

    def test_final_policy_callbacks_see_prepared_nonexecutable_child(self):
        f = self.f; seen = []
        def grant(proposal):
            handles = f.app.runner.handles()
            if proposal.action == 'resume' and handles:
                new = handles[0]
                if new is not f.handle and new._child is not None:
                    seen.append((new.phase, new._child.current(new._child.scope, new.token)))
            return True
        f.owner = TaskContinuationOwner(f.app, authorize=grant)
        new = self.resume_without_advance(f)
        f.app.host.advance_owned(lambda: f.app.runner.advance('5', new.token, 1.))
        self.assertTrue(seen)
        self.assertTrue(all(value == ('prepared', False) for value in seen))
        self.assertFalse(self.requests)

    def test_non_string_notification_kind_cannot_enter_immutable_snapshot(self):
        class EqualKind:
            def __eq__(self, other): return other == 'hint'
        self.install()
        self.assertTrue(self.f.handle._child.notify('unusual', EqualKind(), 0, 'mutable kind'))
        with self.assertRaises(Exception):
            self.f.app.host.pause('5', timeout=0)
        self.assertTrue(self.f.owner.retained(self.f.handle))

    def test_direct_replayed_transfer_is_refused_without_duplicate_notifications(self):
        self.install()
        self.f.handle._child.notify('hint', 'hint', 0, 'once')
        old, new = self.pause_resume()
        owner, cap, receipt = new._continuation
        before = deepcopy(new._child._events)
        with self.assertRaises(Exception):
            TaskContinuationOwner._transfer_notifications(owner, cap, new, new._child)
        self.assertEqual(new._child._events, before)
        self.assertTrue(owner.retained(old))
        self.assertTrue(owner.retained(new))
        self.assertFalse(self.requests)


if __name__ == '__main__':
    unittest.main()
