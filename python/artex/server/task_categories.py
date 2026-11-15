"""Authenticated task-category HTTP contracts from server/task_categories.go.

No task execution or network I/O is implemented here. List/create use the injected
repository. Runtime-sensitive mutations require a manager adapter that atomically
orders persistence and live task DTO refreshes, as the Go Manager does. Omitting
that adapter fails closed rather than presenting stale live metadata as updated.

Request bodies are intentionally stricter than Go's single JSON Decode: trailing
JSON documents and non-object bodies are rejected. Error prose is English and
unexpected database/manager errors are redacted instead of leaking internals.
"""
from typing import Mapping, Protocol, Sequence
import re
from .core import Request, Response, Router, error_response, json_response
from ..db.task_categories import (TaskCategory, TaskCategoryStore, CategoryInvalid,
                                  CategoryNameConflict, CategoryNotFound,
                                  CategoryTaskNotFound)

MAX_REQUEST_BYTES = 16 << 10
MAX_NAME_CHARACTERS = 80
MAX_BATCH_SIZE = 100
_INT64 = re.compile(r'[+-]?[0-9]+\Z')
_GO_SPACE = '\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'


class _InvalidRequest(ValueError):
    """Validation messages contain only fixed application text, never user input."""


class TaskCategoryManager(Protocol):
    """Adapter to a live task manager, not a task execution interface.

    Mutations must serialize database writes and live task refreshes using the
    same lock, including rename/delete. task() returns the complete public task
    DTO with resolved status, or None if no live task exists. set_tasks() must be
    atomic and return canonical task IDs actually moved plus the resulting
    category (None when clearing). No mutation may launch or resume a task.
    """
    def task(self, task_id: str) -> Mapping | None: ...
    def rename(self, category_id: int, name: str) -> TaskCategory: ...
    def delete(self, category_id: int) -> bool: ...
    def set_task(self, task_id: str, category_id: int | None) -> TaskCategory | None: ...
    def set_tasks(self, task_ids: Sequence[str], category_id: int | None
                  ) -> tuple[Sequence[str], TaskCategory | None]: ...


def _integer(value: str) -> int | None:
    if not _INT64.fullmatch(value):
        return None
    # Avoid conversion of unbounded digit strings, including many leading zeroes.
    digits = value.lstrip('+-').lstrip('0') or '0'
    if len(digits) > 19:
        return None
    result = int(('-' if value.startswith('-') else '') + digits)
    return result if -(2**63) <= result < 2**63 else None


def normalize_task_ids(raw: Sequence[str]) -> list[tuple[str, bool]]:
    """Trim, canonicalize positive signed-int64 IDs, deduplicate in input order."""
    seen: set[tuple[str, bool]] = set()
    result = []
    for text in raw:
        text = text.strip(_GO_SPACE)
        number = _integer(text)
        valid = number is not None and number > 0
        item = (str(number) if valid else text, valid)
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _category_id(data: dict) -> int | None:
    if 'category_id' not in data:
        raise _InvalidRequest('category_id is required')
    value = data['category_id']
    if value is not None and (type(value) is not int or not 0 < value < 2**63):
        raise _InvalidRequest('Invalid task category ID')
    return value


def _name(data: dict) -> str:
    name = data.get('name')
    if not isinstance(name, str) or not name.strip(_GO_SPACE):
        raise _InvalidRequest('Category name is required')
    if len(name.strip(_GO_SPACE)) > MAX_NAME_CHARACTERS:
        raise _InvalidRequest('Category name must not exceed 80 characters')
    name.encode('utf-8')
    return name


def _category_json(category: TaskCategory | None):
    return category.to_dict() if category is not None else None


class TaskCategoryService:
    def __init__(self, repository: TaskCategoryStore | None,
                 manager: TaskCategoryManager | None = None):
        self.repository = repository
        self.manager = manager

    def register(self, router: Router) -> None:
        """Register six protected routes; never mark any category endpoint public."""
        for method, path, action in (
            ('GET', '/api/task-categories', 'list'),
            ('POST', '/api/task-categories', 'create'),
            ('PATCH', '/api/task-categories/{id}', 'rename'),
            ('DELETE', '/api/task-categories/{id}', 'delete'),
            ('PATCH', '/api/tasks/{id}/category', 'single'),
            ('POST', '/api/tasks/category/batch', 'batch'),
        ):
            router.add(method, path, lambda request, params, action=action:
                       self._handle(action, request, params))

    def _handle(self, action: str, request: Request, params: Mapping[str, str]) -> Response:
        try:
            return self._dispatch(action, request, params)
        except CategoryNameConflict:
            return error_response(409, 'Category name already exists')
        except CategoryInvalid as error:
            return error_response(400, str(error))
        except (CategoryNotFound, CategoryTaskNotFound) as error:
            return error_response(404, str(error))
        except Exception:
            return error_response(500, 'Task category operation failed')

    def _dispatch(self, action: str, request: Request, params: Mapping[str, str]) -> Response:
        if action in ('list', 'create') and self.repository is None:
            return error_response(503, 'Management data source (PostgreSQL) is not connected')
        if action not in ('list', 'create') and self.manager is None:
            return error_response(503, 'Live task category manager is not configured')
        if action == 'list':
            return json_response(200, {'categories': [x.to_dict() for x in self.repository.list()]})
        category_id = None
        if action in ('rename', 'delete'):
            category_id = _integer(params['id'])
            if category_id is None:
                return error_response(400, 'bad task category id')
        if action == 'delete':
            if not self.manager.delete(category_id):
                return error_response(404, 'task category not found')
            return json_response(200, {'deleted': category_id})
        if action == 'single' and self.manager.task(params['id']) is None:
            return error_response(404, 'task not found')
        # Go's single-task endpoint uses 400 for oversized input; create, rename
        # and batch use 413. Preserve that externally visible distinction.
        if len(request.body) > MAX_REQUEST_BYTES:
            return error_response(400 if action == 'single' else 413, 'Request body too large')
        try:
            data = request.json_object(MAX_REQUEST_BYTES)
            if action in ('create', 'rename'):
                name = _name(data)
            else:
                destination = _category_id(data)
                if action == 'batch':
                    raw = data.get('task_ids')
                    if raw is None:
                        raw = []
                    if not isinstance(raw, list) or any(not isinstance(x, str) for x in raw):
                        raise _InvalidRequest('task_ids must be an array of strings')
                    task_ids = normalize_task_ids(raw)
                    if not 1 <= len(task_ids) <= MAX_BATCH_SIZE:
                        raise _InvalidRequest('task_ids count must be 1-100')
        except _InvalidRequest as error:
            return error_response(400, str(error))
        except (ValueError, UnicodeError):
            return error_response(400, 'Invalid request')
        if action == 'create':
            return json_response(201, self.repository.create(name).to_dict())
        if action == 'rename':
            return json_response(200, self.manager.rename(category_id, name).to_dict())
        if action == 'single':
            self.manager.set_task(params['id'], destination)
            dto = self.manager.task(params['id'])
            if dto is None:
                return error_response(404, 'task not found')
            return json_response(200, dict(dto))
        items, pending = [], []
        for task_id, valid in task_ids:
            item = {'id': task_id, 'ok': False}
            if not valid:
                item['error'] = 'bad task id'
            elif self.manager.task(task_id) is None:
                item['error'] = 'task not found'
            else:
                pending.append(task_id)
            items.append(item)
        category = None
        if pending:
            updated, category = self.manager.set_tasks(pending, destination)
            updated = set(updated)
            for item in items:
                if 'error' not in item:
                    if item['id'] in updated:
                        item['ok'] = True
                    else:
                        item['error'] = 'task not found'
        return json_response(200, {'items': items, 'category': _category_json(category)})
