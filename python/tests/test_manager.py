"""Injected repository/runner fixtures only; no agents, network or PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
import unittest
from artex.manager import TaskManager, RuntimeUnavailable, TaskConflict, CancellationToken

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def record(task_id=1, **changes):
    row = dict(id=task_id, exploration_id=task_id, name='', description='authorized fixture',
               goal='test', status='created', created_at=NOW, completed_at=None,
               paused=False, queued=False, pinned_at=None, category_id=None,
               category_name='', source_task_ids=[], company_ids=[], llm_profile_ids=[],
               llm_failover_state='', coverage_enabled=True, parent_ref='PRIVATE')
    row.update(changes)
    return row


def enrich(dto):
    return {**dto, 'last_activity_unix': 1, 'tokens': {'input_tokens': 3},
            'goals_total': 2, 'goals_met': 1, 'in_flight': 0, 'findings': {'high': 1},
            'unsafe_internal_field': 'must not leak'}


class Repository:
    def __init__(self, *rows):
        self.rows = {int(row['id']): deepcopy(row) for row in (rows or (record(),))}
        self.created = 0
        self.fail_pause = False
    def list(self): return deepcopy(list(self.rows.values()))
    def get(self, key): return deepcopy(self.rows.get(key))
    def create(self, description, goal, options):
        self.created += 1
        key = max(self.rows, default=0) + 1
        self.rows[key] = record(key, description=description, goal=goal)
        return self.get(key)
    def apply_admission(self, key, expected, status, queued, mode, preserve):
        row = self.rows[key]
        if row['status'] != expected: return None
        row.update(status=status, queued=queued, paused=False)
        return self.get(key)
    def apply_pause(self, key):
        if self.fail_pause: raise RuntimeError('fixture DB failure')
        row = self.rows[key]
        if row['paused'] or row['status'] in ('done','failed','timeout'): return None
        row.update(paused=True, queued=False, queued_at=None)
        return self.get(key)
    def set_terminal_status_guarded(self, key, status):
        if self.rows[key]['status'] in ('done','failed','timeout'): return False
        self.rows[key].update(status=status, completed_at=NOW)
        return True
    def update(self, key, **patch):
        row = self.rows[key]
        if 'name' in patch: row['name'] = patch['name']
        if 'pinned' in patch: row['pinned_at'] = NOW if patch['pinned'] else None
        return self.get(key)


class Handle:
    def __init__(self): self.stopped = True; self.causes = []
    def cancel(self, reason): self.causes.append(reason)
    def wait(self, timeout): return self.stopped


class Runner:
    def __init__(self):
        self.calls = 0; self.tokens = []; self.handles = []
        self.entered = Event(); self.release = None; self.fail = False; self.available = True
    def validate_create(self, description, goal, options, seed):
        if seed: raise RuntimeUnavailable('fixture cannot seed intentions')
    def ready(self, row): return self.available
    def start(self, row, token):
        self.calls += 1; self.tokens.append(token); self.entered.set()
        if self.release is not None: self.release.wait(5)
        if self.fail: raise RuntimeError('fixture start failed with no live work')
        handle = Handle(); self.handles.append(handle); return handle


class Categories:
    def __init__(self): self.fail = False; self.moved = []
    def rename(self, key, name):
        if self.fail: raise RuntimeError('fixture write failed')
        return SimpleNamespace(id=key, name=name)
    def delete(self, key): return True
    def set_task(self, key, category): return None if category is None else SimpleNamespace(id=category, name='moved')
    def set_tasks(self, keys, category):
        self.moved = list(keys)
        return keys[:-1], self.set_task(0, category)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.repository, self.runner, self.categories = Repository(), Runner(), Categories()
        self.manager = TaskManager(self.repository, self.categories, self.runner, dto_enricher=enrich)
        self.manager.load_existing()

    def test_loading_never_starts_and_status_ignores_persisted_running(self):
        self.repository.rows[1]['status'] = 'running'
        manager = TaskManager(self.repository, runner=self.runner)
        manager.load_existing()
        self.assertEqual(manager.status('1'), 'created')
        self.assertEqual(self.runner.calls, 0)

    def test_dto_whitelist_copies_and_no_fake_metrics(self):
        dto = self.manager.task('1')
        self.assertEqual(dto['created_unix'], int(NOW.timestamp()))
        self.assertEqual(dto['tokens']['input_tokens'], 3)
        self.assertNotIn('parent_ref', dto)
        self.assertNotIn('unsafe_internal_field', dto)
        dto['company_ids'].append(9)
        self.assertEqual(self.manager.task('1')['company_ids'], [])
        self.manager.dto_enricher = None
        with self.assertRaises(RuntimeUnavailable): self.manager.task('1')

    def test_order_active_and_metadata(self):
        self.repository.rows[2] = record(2)
        self.manager.load_existing()
        self.assertEqual([x['id'] for x in self.manager.list_tasks()], ['2','1'])
        self.manager.update_metadata('1', name='renamed', pinned=True)
        self.assertEqual(self.manager.list_tasks()[0]['name'], 'renamed')
        self.assertEqual(self.runner.calls, 0)

    def test_create_missing_dependencies_writes_nothing(self):
        self.manager.runner = None
        with self.assertRaises(RuntimeUnavailable): self.manager.create_task('x','y')
        self.assertEqual(self.repository.created, 0)
        self.manager.runner = self.runner
        with self.assertRaises(RuntimeUnavailable): self.manager.create_task('x','y', seed_first_intent=True)
        with self.assertRaises(RuntimeUnavailable): self.manager.create_task('x','y', {'llm_profile_ids':[7]})
        self.assertEqual(self.repository.created, 0)

    def test_unready_preflight_creates_no_record(self):
        self.runner.available = False
        with self.assertRaises(RuntimeUnavailable):
            self.manager.create_task('x', 'y')
        self.assertEqual(self.repository.created, 0)

    def test_create_starts_only_injected_runner(self):
        dto = self.manager.create_task('x','y')
        self.assertEqual(dto['status'], 'running')
        self.assertEqual(self.manager.active_task_id(), '2')
        self.assertEqual(self.runner.calls, 1)

    def test_duplicate_concurrent_start_is_single_runner(self):
        self.runner.release = Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.manager.start, '1')
            self.assertTrue(self.runner.entered.wait(2))
            self.assertFalse(pool.submit(self.manager.start, '1').result(2))
            self.runner.release.set()
            self.assertTrue(first.result(2))
        self.assertEqual(self.runner.calls, 1)

    def test_pause_during_start_keeps_cancellation_gate(self):
        self.runner.release = Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            starting = pool.submit(self.manager.start, '1')
            self.assertTrue(self.runner.entered.wait(2))
            with self.assertRaises(TaskConflict): self.manager.pause('1', timeout=0)
            with self.assertRaises(TaskConflict): self.manager.start('1')
            self.runner.release.set()
            self.assertFalse(starting.result(2))
        self.assertEqual(self.manager.pause('1')['status'], 'paused')
        self.assertTrue(self.runner.tokens[0].cancelled)

    def test_pause_requires_drain_then_resume_new_generation(self):
        self.manager.start('1')
        old = self.runner.tokens[0]
        self.runner.handles[0].stopped = False
        with self.assertRaises(TaskConflict): self.manager.pause('1', timeout=0)
        self.assertFalse(self.repository.rows[1]['paused'])
        self.runner.handles[0].stopped = True
        self.manager.pause('1')
        self.assertEqual(self.manager.control_task('1','resume')['status'], 'running')
        self.assertIsNot(old, self.runner.tokens[-1])
        self.assertFalse(self.manager.complete('1', old))

    def test_pause_persistence_failure_remains_gated(self):
        self.manager.start('1'); self.repository.fail_pause = True
        with self.assertRaises(RuntimeError): self.manager.pause('1')
        with self.assertRaises(TaskConflict): self.manager.start('1')
        self.repository.fail_pause = False
        self.assertEqual(self.manager.pause('1')['status'], 'paused')

    def test_start_failure_compensates_to_paused(self):
        self.runner.fail = True
        with self.assertRaises(RuntimeError): self.manager.start('1')
        self.assertTrue(self.repository.rows[1]['paused'])
        self.assertEqual(self.manager.status('1'), 'paused')

    def test_terminal_first_writer_and_stale_completion(self):
        self.manager.start('1')
        token = self.runner.tokens[0]
        self.assertTrue(self.manager.complete('1', token, 'done'))
        self.assertFalse(self.manager.complete('1', token, 'timeout'))
        self.assertEqual(self.manager.status('1'), 'done')
        with self.assertRaises(TaskConflict): self.manager.pause('1')

    def test_category_refresh_failure_and_batch_subset(self):
        self.manager.set_task('1', 3)
        self.manager.rename(3,'new name')
        self.assertEqual(self.manager.task('1')['category_name'], 'new name')
        self.categories.fail = True
        with self.assertRaises(RuntimeError): self.manager.rename(3,'wrong')
        self.assertEqual(self.manager.task('1')['category_name'], 'new name')
        self.categories.fail = False
        self.repository.rows[2] = record(2); self.manager.load_existing()
        updated, _ = self.manager.set_tasks(['1','2'], 4)
        self.assertEqual(updated, ['1'])
        self.assertEqual(self.manager.task('1')['category_id'], 4)
        self.assertNotIn('category_id', self.manager.task('2'))
        self.manager.delete(4)
        self.assertNotIn('category_id', self.manager.task('1'))

    def test_queued_pause_never_runs(self):
        self.repository.rows[1]['queued'] = True
        manager = TaskManager(self.repository, runner=self.runner, dto_enricher=enrich)
        manager.load_existing()
        self.assertEqual(manager.status('1'), 'queued')
        with self.assertRaises(TaskConflict): manager.start('1')
        self.assertEqual(manager.pause('1')['status'], 'paused')
        self.assertEqual(self.runner.calls, 0)

    def test_cancellation_first_cause_wins(self):
        token = CancellationToken(); token.cancel('first'); token.cancel('second')
        self.assertEqual(token.reason, 'first')
        with self.assertRaises(TaskConflict): token.check()


if __name__ == '__main__': unittest.main()
