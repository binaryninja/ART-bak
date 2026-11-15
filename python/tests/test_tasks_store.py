from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import unittest
from artex.db.tasks import Task, TaskStore, TaskCreateOptions, TaskInvalid, TaskSourceNotFound, TaskCompanyNotFound
from artex.db.task_categories import CategoryNotFound

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
ROW = (10, 'Name', None, '', 'Description', 'Goal', 20, 'created', False, False,
       None, '', None, None, '', None, NOW, None, 0, 600, True, None, None)


class Fake:
    def __init__(self, results=()):
        self.results, self.calls = list(results), []

    def call(self, mode, query):
        self.calls.append((mode, query))
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_all(self, query):
        return self.call('all', query)

    def fetch_one(self, query):
        return self.call('one', query)

    def execute(self, query):
        return self.call('execute', query)

    @contextmanager
    def transaction(self):
        self.calls.append(('begin', None))
        try:
            yield self
        except BaseException:
            self.calls.append(('rollback', None))
            raise
        else:
            self.calls.append(('commit', None))


class TasksStoreTests(unittest.TestCase):
    def test_row_complete_and_serialization(self):
        task = Task(*ROW)
        self.assertEqual(task.exploration_id, 20)
        self.assertFalse(task.pinned)
        self.assertEqual(task.to_dict()['created_at'], NOW.isoformat())
        self.assertNotIn('llm_chain_revision', task.to_dict())
        self.assertNotIn('category_id', task.to_dict())
        self.assertIn('coverage_enabled', task.to_dict())
        self.assertEqual(len(ROW), 23)

    def test_list_bulk_hydration_exhausted_cursor_not_repaired(self):
        db = Fake([[ROW], [(10, 5, None, 3, 5, 0, 'ready', '', None),
                          (10, 5, None, 3, 6, 1, 'quota_exhausted', 'no credits', NOW)],
                   [(10, 8)], [(10, 4)]])
        task = TaskStore(db).list()[0]
        self.assertEqual(task.llm_profile_ids, [5, 6])
        self.assertIsNone(task.active_llm_profile_id)
        self.assertEqual(task.llm_failover_state, 'chain_exhausted')
        self.assertEqual(task.llm_failover_reason, 'no credits')
        self.assertEqual(task.source_task_ids, [8])
        self.assertEqual(task.company_ids, [4])
        self.assertEqual(len(db.calls), 4)
        self.assertIn('pinned_at DESC NULLS LAST, id DESC', db.calls[0][1].sql)
        self.assertTrue(all(query.params == ([10],) for _, query in db.calls[1:]))

    def test_get_missing_empty_list_and_active_ready(self):
        db = Fake([[], []])
        store = TaskStore(db)
        self.assertIsNone(store.get(100))
        self.assertEqual(store.list(), [])
        self.assertEqual(len(db.calls), 2)
        db = Fake([[ROW], [(10, 5, 5, 0, 5, 0, 'ready', '', None)], [], []])
        self.assertEqual(TaskStore(db).get(10).llm_failover_state, 'ready')
        self.assertIn('deleted_at IS NULL', db.calls[0][1].sql)

    def test_create_atomic_defaults_root_and_sources_profiles(self):
        db = Fake([(20,), 1, ROW, 1, 1, 1])
        opts = TaskCreateOptions(name='Name', timeout_seconds=-1, plan_heartbeat_seconds=300,
                                 source_task_ids=[8], llm_profile_ids=[5, 6])
        task = TaskStore(db).create('Description', 'Goal', opts)
        self.assertEqual(task.source_task_ids, [8])
        self.assertEqual(task.llm_profile_ids, [5, 6])
        self.assertEqual(db.calls[0][0], 'begin')
        self.assertEqual(db.calls[-1][0], 'commit')
        self.assertEqual(db.calls[1][1].params, ('Description', 'Goal'))
        payload = json.loads(db.calls[2][1].params[1])
        self.assertEqual(payload['goal'], 'Goal')
        self.assertIn("'fact'", db.calls[2][1].sql)
        self.assertIn("'origin'", db.calls[2][1].sql)
        self.assertEqual(db.calls[3][1].params, ('Name', None, 'Description', 'Goal', 20, 5, 0, 600, True))
        self.assertEqual(db.calls[4][1].params, (10, 8))
        self.assertEqual(db.calls[5][1].params, (10, 5, 0))
        self.assertEqual(db.calls[6][1].params, (10, 6, 1))
        self.assertEqual(opts.timeout_seconds, -1)  # Caller options not mutated.

    def test_create_missing_category_or_source_rolls_back(self):
        db = Fake([(20,), 1, []])
        with self.assertRaises(CategoryNotFound):
            TaskStore(db).create('D', 'G', TaskCreateOptions(category_id=7))
        self.assertEqual(db.calls[-1][0], 'rollback')
        self.assertIn('FOR KEY SHARE', db.calls[-2][1].sql)
        db = Fake([(20,), 1, ROW, 0])
        with self.assertRaises(TaskSourceNotFound):
            TaskStore(db).create('D', 'G', TaskCreateOptions(source_task_ids=[8]))
        self.assertEqual(db.calls[-1][0], 'rollback')

    def test_company_snapshot_order_and_count(self):
        db = Fake([(20,), 1, ROW, (None,), (2,), 2, 2])
        task = TaskStore(db).create('D', 'G', TaskCreateOptions(company_ids=[9, 7, 9], coverage_enabled=False))
        self.assertEqual(task.company_ids, [9, 7])
        self.assertEqual(db.calls[3][1].params[-1], False)
        self.assertEqual(db.calls[4][1].params, (7337741003,))
        self.assertIn('pg_advisory_xact_lock', db.calls[4][1].sql)
        self.assertIn('INSERT INTO task_scope', db.calls[5][1].sql)
        self.assertIn('UPDATE assets', db.calls[6][1].sql)
        self.assertIn('INSERT INTO task_asset_links', db.calls[7][1].sql)
        self.assertEqual(db.calls[7][1].params, (10, [9, 7], 'company'))
        db = Fake([(20,), 1, ROW, (None,), (0,)])
        with self.assertRaises(TaskCompanyNotFound):
            TaskStore(db).create('D', 'G', TaskCreateOptions(company_ids=[999]))
        self.assertEqual(db.calls[-1][0], 'rollback')

    def test_create_invalid_and_unsupported_before_writes(self):
        db = Fake()
        options = [TaskCreateOptions(source_task_ids=[1]*9), TaskCreateOptions(source_task_ids=[1,1]),
                   TaskCreateOptions(company_ids=list(range(1,34))), TaskCreateOptions(llm_profile_ids=[0]),
                   TaskCreateOptions(coverage_enabled='false')]
        for opts in options:
            with self.assertRaises(ValueError):
                TaskStore(db).create('D', 'G', opts)
        with self.assertRaises(NotImplementedError):
            TaskStore(db).create('D', 'G', TaskCreateOptions(intercept_rules=[{'action':'deny'}]))
        with self.assertRaises(NotImplementedError):
            TaskStore(db).delete(10)
        self.assertEqual(db.calls, [])

    def test_creation_profile_failure_and_commit_failure_propagate(self):
        db = Fake([(20,), 1, ROW, RuntimeError('foreign key violation')])
        with self.assertRaisesRegex(RuntimeError, 'foreign key'):
            TaskStore(db).create('D', 'G', TaskCreateOptions(llm_profile_ids=[999]))
        self.assertEqual(db.calls[-1][0], 'rollback')
        class CommitFailure(Fake):
            @contextmanager
            def transaction(self):
                yield self
                raise RuntimeError('commit failed')
        with self.assertRaisesRegex(RuntimeError, 'commit failed'):
            TaskStore(CommitFailure([(20,), 1, ROW])).create('D', 'G')

    def test_metadata_preserves_pin_and_missing(self):
        db = Fake([[ROW], []])
        store = TaskStore(db)
        self.assertIsNotNone(store.update(10, name='', pinned=True))
        self.assertEqual(db.calls[0][1].params, (10, True, '', True))
        self.assertIn('COALESCE(pinned_at, now())', db.calls[0][1].sql)
        self.assertIsNone(store.update(99))
        self.assertEqual(db.calls[1][1].params, (99, False, None, None))

    def test_status_guard_queue_and_clock_sql(self):
        db = Fake([1, 0, 1, 1, 1, (NOW,)])
        store = TaskStore(db)
        store.set_status(10, 'running')
        self.assertIn('ELSE NULL END', db.calls[-1][1].sql)
        self.assertFalse(store.set_terminal_status_guarded(10, 'done'))
        self.assertTrue(store.set_terminal_status_guarded(10, 'timeout'))
        self.assertIn("status NOT IN ('done','failed','timeout')", db.calls[-1][1].sql)
        store.enqueue(10, 'bootstrap')
        self.assertIn('WHEN queued THEN COALESCE(queued_at, now())', db.calls[-1][1].sql)
        store.dequeue(10, False)
        self.assertEqual(db.calls[-1][1].params, (10, False))
        self.assertEqual(store.stamp_first_run(10, 300), NOW)
        self.assertIn('WHEN first_run_at IS NOT NULL THEN deadline_at', db.calls[-1][1].sql)
        for action in (lambda: store.set_status(10,'invalid'), lambda: store.set_terminal_status_guarded(10,'running'), lambda: store.enqueue(10,'bad')):
            with self.assertRaises(TaskInvalid):
                action()

    def test_atomic_pause_and_admission_compare(self):
        db = Fake([[ROW], [ROW], []])
        store = TaskStore(db)
        self.assertIsNotNone(store.apply_pause(10))
        self.assertIn('paused=true, queued=false, queued_at=NULL', db.calls[0][1].sql)
        self.assertIn('paused=false AND status NOT IN', db.calls[0][1].sql)
        self.assertIsNotNone(store.apply_admission(10, 'timeout', queued=True, mode='resume', preserve_position=True))
        self.assertEqual(db.calls[1][1].params, (10, 'running', True, 'resume', True, 'timeout'))
        self.assertIn('AND status=$6', db.calls[1][1].sql)
        self.assertIn("WHEN $6='timeout'", db.calls[1][1].sql)
        self.assertIsNone(store.apply_admission(10, 'paused'))

    def test_bound_injection_and_database_errors_propagate(self):
        db = Fake([RuntimeError('DB failed')])
        with self.assertRaisesRegex(RuntimeError, 'DB failed'):
            TaskStore(db).update(10, name="'; DELETE FROM tasks; --")
        self.assertNotIn('DELETE FROM tasks', db.calls[0][1].sql)
        self.assertEqual(db.calls[0][1].params[2], "'; DELETE FROM tasks; --")


if __name__ == '__main__':
    unittest.main()
