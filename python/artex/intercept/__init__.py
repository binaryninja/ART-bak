"""Tool-call interception domain. No tools are executed by this package.

Storage and model callbacks are injected. See PORT_STATUS.md for integration and
intentional fail-closed differences from the Go implementation.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import re
from threading import Event, RLock
import time
from typing import Any, Protocol

DEFAULT_ENABLED_TOOLS = ('Bash', 'WebFetch', 'web_search', 'shell_open', 'shell_send', 'Write', 'Edit', 'MultiEdit')
ACTIONS = {'allow', 'ask', 'deny'}


def bounded(text: str, limit: int) -> tuple[str, bool]:
    if limit < 0:
        raise ValueError('limit must be nonnegative')
    raw = text.encode('utf-8')
    return raw[:limit].decode('utf-8', errors='ignore'), len(raw) > limit


def _reject_constant(value):
    raise ValueError('Non-finite JSON number: ' + value)


def _loads(raw):
    return json.loads(raw, parse_constant=_reject_constant)


def raw_input(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def digest_input(value: Any) -> str:
    raw = raw_input(value)
    try:
        raw = json.dumps(_loads(raw), sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, UnicodeError):
        pass
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ReviewBackground:
    source: str = ''
    text: str = ''
    truncated: bool = False


@dataclass
class Context:
    working_directory: str = ''
    background: ReviewBackground | None = None
    conversation_id: int = 0
    task_id: str = ''
    agent_name: str = ''
    cancelled: Event | None = None
    emit: Any = None


def build_review_input(context: Context | None, tool: str, arguments: Any) -> dict:
    result = {'version': 4, 'tool_name': tool, 'arguments': _loads(raw_input(arguments))}
    if context:
        if context.working_directory:
            result['working_directory'] = context.working_directory
        bg = context.background
        if bg and bg.source == 'user_message' and bg.text.strip():
            text, cut = bounded(bg.text, 4000)
            result['background'] = {'source': bg.source, 'text': text}
            if cut or bg.truncated:
                result['background']['truncated'] = True
    return result


@dataclass(frozen=True)
class Verdict:
    action: str = ''
    reason: str = ''


def parse_verdict(text: str) -> Verdict:
    text = text.strip()
    if len(text) > 6 and text.startswith('```') and text.endswith('```'):
        text = text[3:-3].strip()
        if not text.startswith('{') and '\n' in text:
            text = text.split('\n', 1)[1].strip()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate key')
            result[key] = value
        return result
    try:
        data = json.loads(text, object_pairs_hook=pairs, parse_constant=_reject_constant)
        if not isinstance(data, dict) or set(data) != {'decision', 'comment'}:
            return Verdict()
        action, reason = data['decision'], data['comment']
        if not isinstance(action, str) or action not in ACTIONS or not isinstance(reason, str):
            return Verdict()
        reason = reason.strip()
        if len(reason.encode()) > 2400:
            return Verdict()
        # Legacy persisted replies remain readable; new replies use English.
        formats = [('Operation:', '; Consequence:', '; Rule:'),
                   ('\u5b9e\u9645\u64cd\u4f5c\uff1a', '\uff1b\u6210\u529f\u540e\u7684\u540e\u679c\uff1a', '\uff1b\u547d\u4e2d\u89c4\u5219\uff1a')]
        for prefix, separator, end in formats:
            if reason.startswith(prefix):
                operation, found, rest = reason[len(prefix):].partition(separator)
                consequence, found_end, rule = rest.partition(end)
                if found and found_end and all(x.strip() for x in (operation, consequence, rule)):
                    return Verdict(action, reason)
    except (ValueError, TypeError):
        pass
    return Verdict()


@dataclass(frozen=True)
class Rule:
    id: int = 0
    name: str = ''
    enabled: bool = True
    priority: int = 0
    match_target: str = 'tool_name'
    match_type: str = 'contains'
    pattern: str = ''
    action: str = 'ask'
    message: str = ''
    timeout_enabled: bool = False
    timeout_seconds: float = 60
    timeout_action: str = 'deny'


@dataclass(frozen=True)
class Decision:
    action: str = ''
    message: str = ''
    rule_id: int = 0
    rule_name: str = ''
    timeout_enabled: bool = False
    timeout_seconds: float = 60
    timeout_action: str = 'deny'
    model_fallback: bool = False
    profile_id: int = 0
    model_input: dict | None = None
    model_input_digest: str = ''


class Store(Protocol):
    """resolve_pending must atomically compare status='pending' and update it."""
    def list_rules(self) -> list[Rule]: ...
    def get_enabled_tools(self) -> list[str] | None: ...
    def create_pending(self, context, decision, tool, arguments) -> int: ...
    def get_pending(self, pending_id: int) -> dict: ...
    def resolve_pending(self, pending_id: int, status: str, action: str, reason: str) -> bool: ...
    def log_decision(self, context, decision, tool, arguments, status): ...


class AlreadyDecided(ValueError):
    pass


class Interceptor:
    """Adapter for artex.guard; database adapters remain application-owned.

    reviewer(context, tool, complete_review_input) returns Decision. Its adapter
    MUST enforce its own model timeout; no hidden worker threads are spawned.
    """
    def __init__(self, store: Store, reviewer=None, *, fail_action='deny'):
        if fail_action not in ACTIONS:
            raise ValueError('Invalid failure action')
        self.store, self.reviewer, self.fail_action = store, reviewer, fail_action
        self._lock = RLock()
        self._rules = self._tools = None
        self._pending = {}

    def invalidate(self):
        with self._lock:
            self._rules = self._tools = None

    def _load(self):
        with self._lock:
            if self._rules is None:
                rules = sorted(self.store.list_rules(), key=lambda r: -r.priority)
                compiled = []
                for rule in rules:
                    if not rule.enabled:
                        continue
                    if rule.action not in ACTIONS or rule.match_target not in {'tool_name', 'tool_input'}:
                        raise ValueError('Invalid interception rule')
                    expression = re.compile(rule.pattern) if rule.match_type == 'regex' else None
                    compiled.append((rule, expression))
                tools = self.store.get_enabled_tools()
                if tools is not None and (not isinstance(tools, list) or not all(isinstance(t, str) for t in tools)):
                    raise ValueError('Invalid enabled tools configuration')
                self._rules, self._tools = compiled, set(DEFAULT_ENABLED_TOOLS if tools is None else tools)

    def is_tool_enabled(self, tool):
        try:
            self._load()
        except Exception:
            return True  # Keep failed configuration inside enforcement.
        return tool in self._tools

    def match(self, tool, arguments):
        try:
            self._load()
            subject_input = raw_input(arguments).decode('utf-8')
            for r, pattern in self._rules:
                subject = tool if r.match_target == 'tool_name' else subject_input
                if (pattern.search(subject) if pattern else r.pattern in subject):
                    return Decision(action=r.action, message=r.message or f'Interception rule [{r.name}]: {r.action}',
                                    rule_id=r.id, rule_name=r.name, timeout_enabled=r.timeout_enabled,
                                    timeout_seconds=r.timeout_seconds, timeout_action=r.timeout_action), True
        except Exception as exc:
            return Decision('deny', 'Interception configuration unavailable: ' + str(exc)), True
        return Decision(), False

    def judge(self, context, tool, arguments):
        if self.reviewer is None:
            return Decision(), False
        try:
            review = build_review_input(context, tool, arguments)
        except (ValueError, UnicodeError, TypeError) as exc:
            return Decision('ask', 'Invalid review input: ' + str(exc), timeout_enabled=True,
                            timeout_seconds=300, model_fallback=True), True
        try:
            decision = self.reviewer(context, tool, review)
            if not isinstance(decision, Decision) or decision.action not in ACTIONS:
                raise ValueError('Unparseable verdict')
        except Exception as exc:
            decision = Decision(self.fail_action, 'Model review failed: ' + str(exc), model_fallback=True)
        if decision.action == 'ask' and not decision.timeout_enabled:
            decision = replace(decision, timeout_enabled=True, timeout_seconds=300, timeout_action='deny')
        return replace(decision, rule_id=0, rule_name='', model_input=review, model_input_digest=digest_input(review)), True

    def log(self, context, decision, tool, arguments, status):
        try:
            self.store.log_decision(context, decision, tool, raw_input(arguments), status)
        except Exception:
            pass  # Auditing cannot change an existing verdict.

    def decide(self, pending_id, allowed):
        if not isinstance(allowed, bool):
            raise TypeError('Approval must be a boolean')
        action = 'allow' if allowed else 'deny'
        if not self.store.resolve_pending(pending_id, 'allowed' if allowed else 'denied', action, 'Human decision'):
            raise AlreadyDecided('Approval already resolved or missing')
        with self._lock:
            if pending_id in self._pending:
                self._pending[pending_id].set()

    def handle_ask(self, context, decision, tool, arguments):
        context = context or Context()
        if context.cancelled and context.cancelled.is_set():
            return False
        pending_id = None
        try:
            pending_id = self.store.create_pending(context, decision, tool, raw_input(arguments))
            event = Event()
            with self._lock:
                self._pending[pending_id] = event
            if context.emit:
                context.emit({'kind': 'intercept_request', 'pending_id': pending_id, 'tool': tool})
            seconds = decision.timeout_seconds if decision.timeout_seconds > 0 else 60
            deadline = time.monotonic() + seconds if decision.timeout_enabled else None
            while True:
                if context.cancelled and context.cancelled.is_set():
                    self.store.resolve_pending(pending_id, 'denied', 'deny', 'Work cancelled')
                    return False
                # Re-read after registration: handles decisions between INSERT and registration.
                detail = self.store.get_pending(pending_id)
                if detail['status'] != 'pending':
                    return detail['status'] == 'allowed' or detail.get('effective_action') == 'allow'
                if deadline is not None and time.monotonic() >= deadline:
                    action = 'allow' if decision.timeout_action == 'allow' else 'deny'
                    if self.store.resolve_pending(pending_id, 'timeout', action, 'Approval timed out'):
                        return action == 'allow'
                    continue  # A concurrent human decision won the atomic update.
                event.wait(min(.05, max(0, deadline-time.monotonic())) if deadline else .05)
                event.clear()
        except Exception:
            return False
        finally:
            with self._lock:
                self._pending.pop(pending_id, None)
