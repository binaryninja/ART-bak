"""Protected task HTTP slice, matching the actual Go routes.

GET/POST /api/tasks, GET /api/tasks/{id}, and POST /api/tasks/{id}/control
are implemented. Status is a TaskDTO field; upstream has no task /status or /stop
endpoint. Control accepts pause/resume only. Pausing requires acknowledged
cooperative cancellation and is not deletion or terminal completion.

POST creation always requests execution, as upstream does. Missing runner,
profile resolver, metrics adapter or resume admission must fail explicitly,
not return a success implying that work started. The injected manager owns
persistence, validation-before-start and ordering with runtime state. This module
never opens a network connection, starts a task runner, or executes a tool itself.

Intentional hardening: 1 MiB JSON request limit (upstream handlers have no local
limit), reject trailing JSON and invalid UTF-8, and redact unexpected errors.
"""
from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from .core import Request, Response, Router, error_response, json_response
from ..db.tasks import TaskCreateOptions, TaskInvalid, TaskSourceNotFound, TaskCompanyNotFound
from ..db.task_categories import CategoryInvalid, CategoryNotFound

MAX_REQUEST_BYTES = 1 << 20
MAX_SOURCE_COUNT = 8
MAX_COMPANY_COUNT = 32
_GO_SPACE = '\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'
_ID = re.compile(r'[+-]?[0-9]+\Z')


def _invalid_number(_value):
    raise ValueError('Invalid JSON number')


class InvalidTaskRequest(ValueError):
    """Messages contain application labels, not request values or credentials."""


class TaskHTTPManager(Protocol):
    """Manager must return complete public TaskDTOs, never raw database rows.

    list_tasks/task must include real metrics/archive information through their
    configured enrichment adapter or explicitly report runtime unavailability.
    create_task must preflight all requested scopes, profile chains and runner
    support BEFORE persistence; it may not silently drop unsupported options.
    control_task must preserve cancellation and paused-only resume semantics.
    """
    def list_tasks(self) -> list[dict]: ...
    def active_task_id(self) -> str: ...
    def task(self, task_id: str) -> Mapping | None: ...
    def create_task(self, description: str, goal: str, options: TaskCreateOptions,
                    *, start: bool = True, seed_first_intent: bool = False) -> Mapping: ...
    def control_task(self, task_id: str, action: str) -> Mapping: ...


@dataclass(frozen=True)
class CreateRequest:
    description: str
    goal: str
    options: TaskCreateOptions
    seed_first_intent: bool


def _string(data: dict, name: str, default: str = '') -> str:
    value = data.get(name)
    if value is None:
        return default
    if not isinstance(value, str):
        raise InvalidTaskRequest(f'{name} must be a string')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise InvalidTaskRequest(f'{name} must be valid UTF-8') from None
    return value


def _integer(value, name: str, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise InvalidTaskRequest(f'{name} must be a signed 64-bit integer')
    return value


def _boolean(data: dict, name: str) -> bool | None:
    value = data.get(name)
    if value is not None and type(value) is not bool:
        raise InvalidTaskRequest(f'{name} must be a boolean')
    return value


def _array(data: dict, name: str) -> list:
    value = data.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidTaskRequest(f'{name} must be an array')
    return value


def _positive_ids(raw: list, name: str, *, deduplicate: bool = False) -> list[int]:
    result, seen = [], set()
    for value in raw:
        number = _integer(value, name)
        if number is None or number <= 0 or (not deduplicate and number in seen):
            raise InvalidTaskRequest(f'{name} contains invalid or duplicate IDs')
        if number not in seen:
            seen.add(number)
            result.append(number)
    return result


def _sources(raw: list) -> list[int]:
    if len(raw) > MAX_SOURCE_COUNT:
        raise InvalidTaskRequest('At most 8 source tasks may be selected')
    result = []
    for value in raw:
        if not isinstance(value, str) or not _ID.fullmatch(value.strip(_GO_SPACE)):
            raise InvalidTaskRequest('Source task IDs are invalid or duplicated')
        value = value.strip(_GO_SPACE)
        digits = value.lstrip('+-').lstrip('0') or '0'
        if len(digits) > 19:
            raise InvalidTaskRequest('Source task IDs are invalid or duplicated')
        number = int(('-' if value.startswith('-') else '') + digits)
        if not 0 < number < 2**63 or number in result:
            raise InvalidTaskRequest('Source task IDs are invalid or duplicated')
        result.append(number)
    return result


def _intercept_rules(raw: list) -> list[dict]:
    rules = []
    for value in raw:
        if not isinstance(value, dict):
            raise InvalidTaskRequest('Task interception rules must be objects')
        enabled = _boolean(value, 'enabled')
        action = _string(value, 'action') or 'block'
        kind = _string(value, 'kind')
        pattern = _string(value, 'pattern').strip(_GO_SPACE)
        note = _string(value, 'note')
        if action not in ('allow', 'block') or not pattern:
            raise InvalidTaskRequest('Invalid task interception action or empty pattern')
        if kind not in ('exact_domain', 'exact_url', 'fuzzy_domain', 'fuzzy_ip', 'fuzzy_url', 'exact_ip', 'cidr'):
            raise InvalidTaskRequest('Invalid task interception kind')
        try:
            if kind in ('exact_ip', 'cidr') and '%' in pattern:
                raise ValueError('Zone identifiers are not accepted')
            if kind == 'exact_ip':
                ipaddress.ip_address(pattern)
            elif kind == 'cidr':
                if '/' not in pattern or not re.fullmatch(r'[0-9]+', pattern.rsplit('/', 1)[1]):
                    raise ValueError('Numeric CIDR prefix required')
                ipaddress.ip_network(pattern, strict=False)
        except ValueError:
            raise InvalidTaskRequest('Invalid task interception IP or CIDR') from None
        rules.append({'enabled': enabled if enabled is not None else False,
                      'action': action, 'kind': kind, 'pattern': pattern, 'note': note})
    return rules


def parse_create_request(data: dict) -> CreateRequest:
    description = _string(data, 'description')
    if not description.strip(_GO_SPACE):
        description = 'Untitled task'
    goal = _string(data, 'goal')
    profiles = _positive_ids(_array(data, 'llm_profile_ids'), 'LLM profile IDs')
    legacy_profile = _integer(data.get('llm_profile_id'), 'llm_profile_id')
    if not profiles and legacy_profile is not None:
        profiles = _positive_ids([legacy_profile], 'LLM profile IDs')
    sources = _sources(_array(data, 'source_task_ids'))
    companies = _positive_ids(_array(data, 'company_ids'), 'Company IDs', deduplicate=True)
    if len(companies) > MAX_COMPANY_COUNT:
        raise InvalidTaskRequest('At most 32 unique companies may be selected')
    category = _integer(data.get('category_id'), 'category_id')
    if category is not None and category <= 0:
        raise InvalidTaskRequest('Task category is invalid')
    timeout = max(0, _integer(data.get('timeout_seconds'), 'timeout_seconds', default=0))
    heartbeat = _integer(data.get('plan_heartbeat_seconds'), 'plan_heartbeat_seconds', default=0)
    options = TaskCreateOptions(name=_string(data, 'name').strip(_GO_SPACE), category_id=category,
        source_task_ids=sources, company_ids=companies, llm_profile_ids=profiles,
        timeout_seconds=timeout, plan_heartbeat_seconds=heartbeat,
        coverage_enabled=_boolean(data, 'coverage_enabled'),
        intercept_rules=_intercept_rules(_array(data, 'intercept_rules')))
    return CreateRequest(description, goal, options, _boolean(data, 'seed_first_intent') is True)


class TaskService:
    def __init__(self, manager: TaskHTTPManager | None,
                 *, profile_available: Callable[[int], bool] | None = None):
        self.manager = manager
        self.profile_available = profile_available

    def register(self, router: Router) -> None:
        for method, path, action in (
            ('GET', '/api/tasks', 'list'), ('POST', '/api/tasks', 'create'),
            ('GET', '/api/tasks/{id}', 'get'), ('POST', '/api/tasks/{id}/control', 'control'),
        ):
            router.add(method, path, lambda request, params, action=action:
                       self._handle(action, request, params))

    def _handle(self, action: str, request: Request, params: Mapping[str, str]) -> Response:
        # Imported here to keep this HTTP contract independent of runner creation.
        from ..manager import RuntimeUnavailable, TaskConflict, TaskNotFound
        if self.manager is None:
            return error_response(503, 'Task manager is not configured')
        try:
            return self._dispatch(action, request, params)
        except RuntimeUnavailable:
            return error_response(503, 'Task runtime or required adapter is unavailable')
        except TaskNotFound:
            return error_response(404, 'task not found')
        except TaskConflict:
            return error_response(409, 'Task lifecycle operation conflicts with its current state')
        except (TaskInvalid, TaskSourceNotFound, TaskCompanyNotFound, CategoryInvalid, CategoryNotFound):
            return error_response(400, 'Task creation options are invalid or unavailable')
        except NotImplementedError:
            return error_response(503, 'Requested task capability is not implemented')
        except Exception:
            return error_response(500, 'Task operation failed')

    def _dispatch(self, action: str, request: Request, params: Mapping[str, str]) -> Response:
        if action == 'list':
            return json_response(200, {'tasks': self.manager.list_tasks(), 'active': self.manager.active_task_id()})
        if action in ('get', 'control'):
            task = self.manager.task(params['id'])
            if task is None:
                return error_response(404, 'task not found')
            if action == 'get':
                return json_response(200, dict(task))
        if len(request.body) > MAX_REQUEST_BYTES:
            return error_response(413, 'Request body too large')
        try:
            # Decode explicitly: json.loads(bytes) otherwise auto-detects UTF-16/32,
            # which the Go JSON endpoint does not accept.
            data = json.loads(request.body.decode('utf-8'), parse_constant=_invalid_number)
            if not isinstance(data, dict):
                raise InvalidTaskRequest('Expected JSON object')
            if action == 'create':
                parsed = parse_create_request(data)
            else:
                command = _string(data, 'action')
                if command not in ('pause', 'resume'):
                    raise InvalidTaskRequest('action must be pause|resume')
        except InvalidTaskRequest as error:
            return error_response(400, str(error))
        except (ValueError, UnicodeError):
            return error_response(400, 'Invalid JSON request')
        if action == 'control':
            result = self.manager.control_task(params['id'], command)
            return json_response(200, dict(result))
        if parsed.options.llm_profile_ids:
            if self.profile_available is None:
                return error_response(503, 'LLM profile validation is unavailable')
            if not all(self.profile_available(x) for x in parsed.options.llm_profile_ids):
                return error_response(400, 'LLM profile does not exist or has no API key')
        if any(self.manager.task(str(x)) is None for x in parsed.options.source_task_ids):
            return error_response(400, 'Source task does not exist')
        created = self.manager.create_task(parsed.description, parsed.goal, parsed.options,
                                           start=True, seed_first_intent=parsed.seed_first_intent)
        return json_response(201, dict(created))
