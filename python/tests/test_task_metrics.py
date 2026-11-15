"""Authored port/fake executor tests; no database or upstream execution."""
from copy import deepcopy
from decimal import Decimal
import unittest
from artex.db.task_metrics import TaskMetricsStore, TaskMetricsEnricher, MetricsUnavailable
from artex.manager import TaskManager


def row(identifier=1, exploration=10, blocker=None):
    return (identifier, exploration, 11, 12, Decimal(13), 14, 150, 5, 3, 2, 1, 2, 3, 4, blocker)


class Executor:
    def __init__(self, rows=None):
        self.rows = [row()] if rows is None else rows
        self.queries = []
    def fetch_all(self, query):
        self.queries.append(query)
        return self.rows


class Repository:
    def list(self):
        return [dict(id=1, exploration_id=10, name='task', description='fixture', goal='',
                     created_at='2026-01-01T00:00:00+00:00', status='created', paused=False,
                     queued=False, company_ids=[7], source_task_ids=[9], coverage_enabled=True)]


class TaskMetricsTests(unittest.TestCase):
    def test_snapshot_maps_metrics_and_blocker(self):
        executor = Executor([row(blocker=23)])
        result = TaskMetricsStore(executor).snapshot(['001', 1])[1]
        self.assertEqual(result['tokens'], dict(input_tokens=11, output_tokens=12, cache_read_tokens=13, cache_write_tokens=14))
        self.assertEqual(result['goals_total'], 5)
        self.assertEqual(result['goals_met'], 3)
        self.assertEqual(result['in_flight'], 2)
        self.assertEqual(result['findings'], dict(critical=1, high=2, medium=3, low=4))
        self.assertEqual(result['archive_blocked_by_task_id'], '23')
        self.assertEqual(executor.queries[0].params, ([1],))

    def test_sql_scope_and_archival_semantics(self):
        executor = Executor()
        TaskMetricsStore(executor).snapshot([1])
        sql = executor.queries[0].sql
        for fragment in ('task.id=ANY($1::bigint[])', 'task.deleted_at IS NULL',
                         "kind='result'", "kind='intent' AND state='running'",
                         'relation.source_task_id=task.id', 'child.deleted_at IS NULL',
                         "pending.state NOT IN ('archive_queued','archiving')", 'MIN(child.id)'):
            self.assertIn(fragment, sql)

    def test_empty_input_no_query(self):
        executor = Executor()
        self.assertEqual(TaskMetricsStore(executor).snapshot([]), {})
        self.assertEqual(executor.queries, [])

    def test_missing_and_duplicate_tasks_fail(self):
        for rows in ([], [row(), row()], [row(identifier=2)]):
            with self.subTest(rows=rows), self.assertRaises(MetricsUnavailable):
                TaskMetricsStore(Executor(rows)).snapshot([1])

    def test_invalid_ids_before_query(self):
        executor = Executor()
        for value in ('1 OR 1=1', True, 0, -1, 2**63):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TaskMetricsStore(executor).snapshot([value])
        self.assertEqual(executor.queries, [])

    def test_database_failure_not_zero_filled(self):
        class Broken:
            def fetch_all(self, query):
                raise RuntimeError('missing schema')
        with self.assertRaisesRegex(RuntimeError, 'missing schema'):
            TaskMetricsStore(Broken()).snapshot([1])

    def test_invalid_metric_rejected(self):
        for invalid in (None, True, -1, Decimal('1.5')):
            values = list(row()); values[2] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(MetricsUnavailable):
                TaskMetricsStore(Executor([values])).snapshot([1])

    def test_live_heartbeat_and_input_immutability(self):
        dto = dict(id='1', exploration_id=10, company_ids=[7])
        before = deepcopy(dto)
        calls = []
        def heartbeat(identifier):
            calls.append(identifier)
            return 200
        enrich = TaskMetricsEnricher(TaskMetricsStore(Executor()), live_last_activity=heartbeat)
        self.assertEqual(enrich(dto)['last_activity_unix'], 200)
        self.assertEqual(calls, ['1'])
        self.assertEqual(dto, before)
        self.assertNotIn('id', enrich(dto))
        self.assertNotIn('company_ids', enrich(dto))

    def test_exploration_mismatch_and_bad_callback_fail(self):
        enrich = TaskMetricsEnricher(TaskMetricsStore(Executor()))
        with self.assertRaises(MetricsUnavailable):
            enrich(dict(id='1', exploration_id=11))
        enrich = TaskMetricsEnricher(TaskMetricsStore(Executor()), live_last_activity=lambda _: None)
        with self.assertRaises(MetricsUnavailable):
            enrich(dict(id='1', exploration_id=10))

    def test_real_manager_boundary_preserves_identity_scope(self):
        enrich = TaskMetricsEnricher(TaskMetricsStore(Executor([row(blocker=23)])))
        manager = TaskManager(Repository(), dto_enricher=enrich)
        manager.load_existing()
        dto = manager.task('1')
        self.assertEqual(dto['id'], '1')
        self.assertEqual(dto['company_ids'], [7])
        self.assertEqual(dto['source_task_ids'], ['9'])
        self.assertEqual(dto['tokens']['input_tokens'], 11)
        self.assertEqual(dto['in_flight'], 2)
        self.assertEqual(dto['archive_blocked_by_task_id'], '23')
        self.assertEqual(dto['status'], 'created')
        self.assertEqual(dto['last_activity_unix'], 150)
