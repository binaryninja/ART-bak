"""Provider-independent, synchronous LLM pool; no transports or SDK execution.

Providers implement complete(context, request) and stream(context, request).
They return an arbitrary completion result / iterable of events and raise
exceptions for failures. Every event, including metadata, closes the retry window.
"""
from dataclasses import dataclass
from threading import Lock
import sys
from typing import Any, Protocol, Iterable

from ..health import Registry, State
from ..failover import should_failover, is_hard_failure, trim_error

RANK_ACTIVE = sys.maxsize - 1


class ExhaustedError(Exception):
    """All eligible profiles failed before producing a result."""


class Provider(Protocol):
    def complete(self, context: Any, request: Any) -> Any: ...
    def stream(self, context: Any, request: Any) -> Iterable[Any]: ...


@dataclass(frozen=True)
class Member:
    id: int
    name: str
    provider: Provider
    model: str = ""
    format: str = ""
    priority: int = 0
    active: bool = False
    rank: int = 0
    window_tokens: int = 0


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _size(value):
    if value is None:
        return 0
    return len(value.encode("utf-8")) if isinstance(value, str) else len(value)


def blocks_length(blocks):
    return sum(sum(_size(_field(b, key, "")) for key in ("text", "thinking", "input", "name"))
               + blocks_length(_field(b, "content", ()) or ()) for b in blocks)


def estimate_tokens(request):
    """Match Go's UTF-8 byte heuristic; input blocks contain raw JSON bytes/text.

    Request may be a mapping or object with lower-case system/messages/tools.
    Tool input_schema is a decoded map (charge 120 bytes per top-level entry).
    """
    n = sum(_size(s) for s in (_field(request, "system", ()) or ()))
    n += sum(blocks_length(_field(m, "content", ()) or ())
             for m in (_field(request, "messages", ()) or ()))
    for tool in (_field(request, "tools", ()) or ()):
        n += _size(_field(tool, "name", "")) + _size(_field(tool, "description", ""))
        n += len(_field(tool, "input_schema", {}) or {}) * 120
    return n * 2 // 7


def rotate_groups(members, n):
    result = []
    i = 0
    while i < len(members):
        j = i + 1
        while j < len(members) and members[j].rank == members[i].rank:
            j += 1
        group = list(members[i:j])
        offset = n % len(group)
        result.extend(group[offset:] + group[:offset])
        i = j
    return result


class Pool:
    """Input chain is already ranked by caller; equal contiguous ranks rotate."""
    def __init__(self, members, health=None):
        self.members = tuple(members)
        if not self.members:
            raise ValueError("Pool requires at least one member; use new() for optional chains")
        self.health = health if health is not None else Registry()
        self._counter = 0
        self._lock = Lock()

    @property
    def head(self):
        return self.members[0]

    def order(self, request):
        estimate = estimate_tokens(request)
        available = [m for m in self.members if not self.health.is_open(m.id)
                     and (m.window_tokens <= 0 or estimate <= m.window_tokens)]
        if not available:
            return [self.head]
        with self._lock:
            counter = self._counter
            self._counter = (counter + 1) % (1 << 64)
        return rotate_groups(available, counter)

    @staticmethod
    def _retry(context, error):
        # An adapter supplies Go ctx.Err semantics via an optional err() method.
        err = getattr(context, "err", None)
        return should_failover(error, context_cancelled=bool(err and err() is not None))

    def complete(self, context, request):
        last = None
        for member in self.order(request):
            try:
                result = member.provider.complete(context, request)
            except Exception as error:
                if not self._retry(context, error):
                    self.health.pass_(member.id)
                    raise
                last = error
                self.health.trip(member.id, trim_error(error), is_hard_failure(error))
            else:
                self.health.pass_(member.id)
                return result
        raise ExhaustedError(f"All LLM profiles unavailable: {last}") from last

    def stream(self, context, request):
        order = self.order(request)  # match Go: choose at call, not first iteration
        return self._stream(order, context, request)

    def _stream(self, order, context, request):
        last = None
        for member in order:
            emitted = False
            iterator = None
            try:
                try:
                    iterator = iter(member.provider.stream(context, request))
                except Exception as error:
                    if not self._retry(context, error):
                        raise
                    last = error
                    self.health.trip(member.id, trim_error(error), is_hard_failure(error))
                    continue
                while True:
                    try:
                        event = next(iterator)
                    except StopIteration:
                        self.health.pass_(member.id)
                        return
                    except Exception as error:
                        if emitted or not self._retry(context, error):
                            raise
                        last = error
                        self.health.trip(member.id, trim_error(error), is_hard_failure(error))
                        break
                    emitted = True
                    yield event  # outside exception handling: caller exit never retries
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
        raise ExhaustedError(f"All LLM profiles unavailable: {last}") from last


def new(members, health=None):
    members = tuple(members)
    return Pool(members, health) if members else None
