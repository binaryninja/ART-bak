"""Persisted task-list metrics and explicit DTO enrichment.

SQL follows db/exploration.go TaskListMetricsAll and db/task_archives.go
TaskArchiveBlockers. Each snapshot is one PostgreSQL statement, including its
archive blocker. Empty aggregates mean zero ONLY for an existing live task;
missing tasks/schema/database errors never become fabricated zeros.

in_flight is persisted running-intent count, as in Go listTasks, NOT a claim
about actual threads or in-memory workers. Optional live_last_activity callback
supplies the engine heartbeat; without it last_activity_unix is persisted-only.
No engine, capture, connection or schema is created here. No runtime execution.
The callable adapter reads one task per call; callers needing bulk lists should
use snapshot(ids). Manager currently calls per DTO, so list enrichment is N+1.
"""
from __future__ import annotations
from collections.abc import Mapping
from decimal import Decimal
from .core import Executor, Query, int64


class MetricsUnavailable(RuntimeError):
    pass


def _number(value, *, nonnegative=True):
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        value = int(value)
    if type(value) is not int or (nonnegative and value < 0):
        raise MetricsUnavailable('Invalid task metric returned by dependency')
    return value


def _task_id(value):
    if isinstance(value, str):
        if not value.isascii() or not value.isdecimal():
            raise ValueError('Task ID must be a positive integer')
        value = value.lstrip('0') or '0'
        if len(value) > 19:
            raise ValueError('Task ID exceeds int64')
        value = int(value)
    value = int64(value, 'task id')
    if value <= 0:
        raise ValueError('Task ID must be positive')
    return value


SNAPSHOT_SQL = """
        SELECT task.id, task.exploration_id,
               COALESCE(token_metrics.input_tokens,0),
               COALESCE(token_metrics.output_tokens,0),
               COALESCE(token_metrics.cache_read_tokens,0),
               COALESCE(token_metrics.cache_write_tokens,0),
               COALESCE(latest_activity.created_at,0),
               COALESCE(goal_metrics.total,0),
               COALESCE(goal_metrics.met,0),
               COALESCE(intent_metrics.running,0),
               COALESCE(finding_metrics.critical,0),
               COALESCE(finding_metrics.high,0),
               COALESCE(finding_metrics.medium,0),
               COALESCE(finding_metrics.low,0), archive_blocker.child_id
        FROM tasks task
        LEFT JOIN LATERAL (
            SELECT SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cache_read_tokens) AS cache_read_tokens,
                   SUM(cache_write_tokens) AS cache_write_tokens
            FROM activity
            WHERE exploration_id=task.exploration_id AND kind='result'
        ) token_metrics ON true
        LEFT JOIN LATERAL (
            SELECT EXTRACT(EPOCH FROM created_at)::bigint AS created_at
            FROM activity
            WHERE exploration_id=task.exploration_id
            ORDER BY created_at DESC
            LIMIT 1
        ) latest_activity ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE state='met') AS met
            FROM exploration_nodes
            WHERE exploration_id=task.exploration_id AND kind='goal'
        ) goal_metrics ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS running
            FROM exploration_nodes
            WHERE exploration_id=task.exploration_id AND kind='intent' AND state='running'
        ) intent_metrics ON true
        LEFT JOIN LATERAL (
            SELECT COUNT(*) FILTER (WHERE severity='critical') AS critical,
                   COUNT(*) FILTER (WHERE severity='high')     AS high,
                   COUNT(*) FILTER (WHERE severity='medium')   AS medium,
                   COUNT(*) FILTER (WHERE severity='low')      AS low
            FROM findings
            WHERE task_id=task.id
        ) finding_metrics ON true
        LEFT JOIN LATERAL (
            SELECT MIN(child.id) AS child_id
            FROM task_relations relation
            JOIN tasks child ON child.id=relation.task_id AND child.deleted_at IS NULL
            LEFT JOIN task_archives pending ON pending.task_id=child.id
            WHERE relation.source_task_id=task.id
              AND (pending.id IS NULL OR pending.state NOT IN ('archive_queued','archiving'))
        ) archive_blocker ON true
        WHERE task.deleted_at IS NULL AND task.id=ANY($1::bigint[])"""


class TaskMetricsStore:
    def __init__(self, executor: Executor):
        self.executor = executor

    def snapshot(self, task_ids):
        ids = list(dict.fromkeys(_task_id(value) for value in task_ids))
        if not ids:
            return {}
        result = {}
        for row in self.executor.fetch_all(Query(SNAPSHOT_SQL, (ids,))):
            if len(row) != 15:
                raise MetricsUnavailable('Invalid task metric row shape')
            task_id, exploration_id, *values = row
            task_id = _task_id(task_id)
            if task_id not in ids or task_id in result:
                raise MetricsUnavailable('Unexpected or duplicate task metric identity')
            exploration_id = _task_id(exploration_id)
            tokens = dict(zip(('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'),
                              (_number(x) for x in values[:4])))
            metric = dict(exploration_id=exploration_id, tokens=tokens,
                          last_activity_unix=_number(values[4], nonnegative=False),
                          goals_total=_number(values[5]), goals_met=_number(values[6]),
                          in_flight=_number(values[7]),
                          findings=dict(zip(('critical', 'high', 'medium', 'low'),
                                            (_number(x) for x in values[8:12]))))
            if metric['goals_met'] > metric['goals_total']:
                raise MetricsUnavailable('Inconsistent task goal counts')
            if values[12] is not None:
                metric['archive_blocked_by_task_id'] = str(_task_id(values[12]))
            result[task_id] = metric
        if set(result) != set(ids):
            raise MetricsUnavailable('Task metrics unavailable for a missing or deleted task')
        return result


class TaskMetricsEnricher:
    """Inject as TaskManager(dto_enricher=TaskMetricsEnricher(store)).

    Callback receives canonical string task ID and must return an integer Unix
    timestamp. Errors propagate. The input DTO is neither mutated nor returned;
    only metrics/archive fields are returned, preserving identity and scope.
    """
    def __init__(self, store: TaskMetricsStore, *, live_last_activity=None):
        self.store, self.live_last_activity = store, live_last_activity

    def __call__(self, dto: Mapping):
        task_id = _task_id(dto['id'])
        metrics = dict(self.store.snapshot([task_id])[task_id])
        if metrics.pop('exploration_id') != _task_id(dto['exploration_id']):
            raise MetricsUnavailable('Task exploration changed during enrichment')
        if self.live_last_activity is not None:
            live = _number(self.live_last_activity(str(task_id)), nonnegative=False)
            metrics['last_activity_unix'] = max(metrics['last_activity_unix'], live)
        return metrics
