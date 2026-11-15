"""PostgreSQL task-category CRUD using bound Query parameters.

Uses the existing task_categories/tasks schema, triggers, and ON DELETE SET NULL
foreign key. No connection is opened here. Batch moves require a real transaction
adapter; they never fall back to independent statements. Unlike upstream's plain
EXISTS, the batch requests a category row lock intended to prevent concurrent
deletion. Lock semantics require live PostgreSQL validation; mock tests verify
SQL order and transaction contracts only.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Protocol
from .core import Executor, Query, int64

MAX_NAME_RUNES = 80
MAX_BATCH_SIZE = 100


class CategoryInvalid(ValueError):
    pass


class CategoryNameConflict(ValueError):
    pass


class CategoryNotFound(LookupError):
    pass


class CategoryTaskNotFound(LookupError):
    pass


class TransactionalExecutor(Executor, Protocol):
    def transaction(self) -> AbstractContextManager[Executor]:
        """Yield transaction-bound executor; commit on success, rollback on error.

        Adapter must propagate query, commit, and rollback errors. No statements
        may escape to an autocommit connection while this scope is active.
        """
        ...


@dataclass(frozen=True)
class TaskCategory:
    id: int
    name: str
    nkey: str
    task_count: int
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        return {'id': self.id, 'name': self.name, 'task_count': self.task_count,
                'created_at': self.created_at.isoformat(),
                'updated_at': self.updated_at.isoformat()}


# Go strings.Fields uses Unicode White_Space, not Python's broader str.isspace.
_SPACE = re.compile('[\\t\\n\\v\\f\\r \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]+')


def normalize_name(name: str) -> tuple[str, str]:
    if not isinstance(name, str):
        raise CategoryInvalid('invalid task category: name must be a string')
    name = ' '.join(part for part in _SPACE.split(name) if part)
    if not name:
        raise CategoryInvalid('invalid task category: name is required')
    if len(name) > MAX_NAME_RUNES:
        raise CategoryInvalid(f'invalid task category: name exceeds {MAX_NAME_RUNES} characters')
    # Go lowercasing maps individual runes, unlike Python contextual sigma and
    # expanding U+0130. Keep simple rune behavior for category uniqueness.
    nkey = ''.join('i' if char == '\u0130' else char.lower() for char in name)
    return name, nkey


def _id(value: int, name: str, *, positive=False) -> int:
    try:
        value = int64(value, name)
    except ValueError as error:
        raise CategoryInvalid(f'invalid task category: {error}') from error
    if positive and value <= 0:
        raise CategoryInvalid(f'invalid task category: {name} must be positive')
    return value


def _unique(error: Exception) -> bool:
    # Common PostgreSQL adapters expose SQLSTATE as sqlstate or pgcode.
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if getattr(error, 'sqlstate', None) == '23505' or getattr(error, 'pgcode', None) == '23505':
            return True
        error = error.__cause__
    return False


_COLS = '''category.id, category.name, category.nkey,
       count(task.id) FILTER (WHERE task.deleted_at IS NULL),
       category.created_at, category.updated_at'''
_GET = f'''SELECT {_COLS}
FROM task_categories category
LEFT JOIN tasks task ON task.category_id=category.id
WHERE category.id=$1
GROUP BY category.id'''


def _optional(executor: Executor, query: Query):
    rows = executor.fetch_all(query)
    if len(rows) > 1:
        raise RuntimeError('category query unexpectedly returned multiple rows')
    return rows[0] if rows else None


class TaskCategoryStore:
    def __init__(self, executor: Executor):
        self.executor = executor

    def create(self, name: str) -> TaskCategory:
        name, nkey = normalize_name(name)
        row = _optional(self.executor, Query('''WITH inserted AS (
    INSERT INTO task_categories(name, nkey)
    VALUES ($1,$2)
    ON CONFLICT (nkey) DO NOTHING
    RETURNING *
)
SELECT inserted.id, inserted.name, inserted.nkey, 0, inserted.created_at, inserted.updated_at
FROM inserted''', (name, nkey)))
        if row is None:
            raise CategoryNameConflict('task category name already exists')
        return TaskCategory(*row)

    def list(self) -> list[TaskCategory]:
        rows = self.executor.fetch_all(Query(f'''SELECT {_COLS}
FROM task_categories category
LEFT JOIN tasks task ON task.category_id=category.id
GROUP BY category.id
ORDER BY category.name, category.id'''))
        return [TaskCategory(*row) for row in rows]

    def get(self, category_id: int) -> TaskCategory | None:
        row = _optional(self.executor, Query(_GET, (_id(category_id, 'category id'),)))
        return TaskCategory(*row) if row is not None else None

    def rename(self, category_id: int, name: str) -> TaskCategory:
        name, nkey = normalize_name(name)
        try:
            row = _optional(self.executor, Query('''WITH updated AS (
    UPDATE task_categories SET name=$2, nkey=$3 WHERE id=$1 RETURNING *
)
SELECT updated.id, updated.name, updated.nkey,
       (SELECT count(*) FROM tasks WHERE category_id=updated.id AND deleted_at IS NULL),
       updated.created_at, updated.updated_at
FROM updated''', (_id(category_id, 'category id'), name, nkey)))
        except Exception as error:
            if _unique(error):
                raise CategoryNameConflict('task category name already exists') from error
            raise
        if row is None:
            raise CategoryNotFound('task category not found')
        return TaskCategory(*row)

    def delete(self, category_id: int) -> bool:
        return self.executor.execute(Query('DELETE FROM task_categories WHERE id=$1',
                                           (_id(category_id, 'category id'),))) > 0

    def set_task(self, task_id: int, category_id: int | None) -> TaskCategory | None:
        task_id = _id(task_id, 'task id')
        if category_id is None:
            rows = self.executor.execute(Query(
                'UPDATE tasks SET category_id=NULL WHERE id=$1 AND deleted_at IS NULL', (task_id,)))
            if rows == 0:
                raise CategoryTaskNotFound('task not found')
            return None
        category_id = _id(category_id, 'category id', positive=True)
        row = _optional(self.executor, Query('''WITH selected AS (
    SELECT * FROM task_categories WHERE id=$2
), updated AS (
    UPDATE tasks SET category_id=$2
    WHERE id=$1 AND deleted_at IS NULL AND EXISTS (SELECT 1 FROM selected)
    RETURNING id
)
SELECT selected.id, selected.name, selected.nkey,
       (SELECT count(*) FROM tasks WHERE category_id=selected.id AND deleted_at IS NULL),
       selected.created_at, selected.updated_at
FROM selected, updated''', (task_id, category_id)))
        if row is None:
            exists = self.executor.fetch_one(Query(
                'SELECT EXISTS(SELECT 1 FROM tasks WHERE id=$1 AND deleted_at IS NULL)', (task_id,)))[0]
            if not exists:
                raise CategoryTaskNotFound('task not found')
            raise CategoryNotFound('task category not found')
        return TaskCategory(*row)

    def set_tasks(self, task_ids: list[int], category_id: int | None) -> tuple[list[int], TaskCategory | None]:
        if not task_ids:
            raise CategoryInvalid('invalid task category: task ids are required')
        if len(task_ids) > MAX_BATCH_SIZE:
            raise CategoryInvalid(f'invalid task category: at most {MAX_BATCH_SIZE} tasks per request')
        ids = [_id(value, 'task id', positive=True) for value in task_ids]
        if category_id is not None:
            category_id = _id(category_id, 'category id', positive=True)
        transaction = getattr(self.executor, 'transaction', None)
        if transaction is None:
            raise TypeError('batch category moves require a transaction-capable executor')
        with transaction() as tx:
            if category_id is not None:
                row = _optional(tx, Query('SELECT id FROM task_categories WHERE id=$1 FOR KEY SHARE', (category_id,)))
                if row is None:
                    raise CategoryNotFound('task category not found')
            rows = tx.fetch_all(Query('''UPDATE tasks SET category_id=$2
WHERE id=ANY($1::bigint[]) AND deleted_at IS NULL
RETURNING id''', (ids, category_id)))
            updated = [int64(row[0], 'task id') for row in rows]
            category = None
            if category_id is not None:
                row = tx.fetch_one(Query(_GET, (category_id,)))
                category = TaskCategory(*row)
        return updated, category
