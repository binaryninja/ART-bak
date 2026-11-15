"""Offline orchestration primitives; not an executable agent/SDK replacement.

No provider, shell, tool execution, or permission-bypass implementation lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable, Iterable, Mapping
import json
import math
import time


@dataclass(frozen=True)
class TaskClock:
    deadline_unix: float = 0
    final: bool = False


@dataclass(frozen=True)
class RunInfo:
    task_id: int = 0
    exploration_id: int = 0
    intent_id: int = 0
    session_id: str = ""


@dataclass(frozen=True)
class AbortCause:
    code: str
    short: str
    text: str


@dataclass(frozen=True)
class RunContext:
    """Explicit immutable metadata, NOT a Go cancellation context emulation."""
    task_clock: TaskClock = TaskClock()
    run_info: RunInfo = RunInfo()
    abort_cause: AbortCause | None = None

    def with_task_clock(self, clock: TaskClock) -> RunContext:
        return replace(self, task_clock=clock)

    def with_run_info(self, info: RunInfo) -> RunContext:
        return replace(self, run_info=info)


def clamp_max_duration(deadline_unix: float, own_budget: float,
                       *, now: Callable[[], float] = time.time) -> tuple[float, bool]:
    """Return seconds and whether the task binds the run; nonpositive = unlimited.

    Matches Go taskclock.go: remaining task time is floored to one second before
    comparison. This is budget calculation, NOT authorization to start expired work.
    """
    if not math.isfinite(deadline_unix) or not math.isfinite(own_budget):
        raise ValueError("budgets and deadlines must be finite")
    if deadline_unix <= 0:
        return own_budget, False
    current = now()
    if not math.isfinite(current):
        raise ValueError("clock must be finite")
    remaining = max(1.0, deadline_unix - current)
    return (min(remaining, own_budget) if own_budget > 0 else remaining,
            own_budget <= 0 or remaining <= own_budget)


@dataclass(frozen=True)
class Constraint:
    kind: str
    text: str


def constraint_block(rows: Iterable[Constraint]) -> str:
    """Render advisory boundaries. Unknown kinds are denied, like upstream.

    Does not catch loading/iteration errors: a store failure must not silently
    discard boundaries. The caller must enforce scope separately at execution.
    """
    allow, deny = [], []
    for row in rows:
        value = row.text.strip()
        if value:
            (allow if row.kind == "allow" else deny).append("- " + value)
    if not allow and not deny:
        return ""
    lines = ["\n\nOperation constraints (highest priority): check every proposed intent "
             "and action against these boundaries; do not proceed if they conflict."]
    if allow:
        lines.extend(["Allowed operations:", *allow])
    if deny:
        lines.extend(["Prohibited operations:", *deny])
    lines.append("Discovering a new target, port, or host does not grant authorization. "
                 "Unless explicitly in scope, record it as out-of-scope and skip it; "
                 "do not derive intents or execute actions against it.")
    return "\n".join(lines)


def seed_unlock_from_history(messages: Iterable[Mapping],
                             unlock_skill: Callable[[str], None] | None) -> None:
    """Replay normalized tool uses from trusted stored history, not arbitrary text.

    Input schema: messages have `tool_uses`, each with `name` and JSON `input`.
    An SDK message adapter is still required. Replays duplicates as Go does.
    """
    if unlock_skill is None:
        return
    for message in messages:
        for block in message.get("tool_uses", ()):
            if not isinstance(block, Mapping) or block.get("name") != "Skill":
                continue
            raw = block.get("input")
            if not isinstance(raw, (str, bytes, bytearray)):
                continue
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(value, dict) and isinstance(value.get("name"), str) and value["name"]:
                unlock_skill(value["name"])


@dataclass
class RunTrace:
    """Latest tool bookkeeping only; consumers must serialize their events."""
    started_at: float | None = None
    id: str = ""
    name: str = ""
    input: str = ""
    at: float | None = None
    pending: bool = False

    def start(self, id: str, name: str, input: str, *, now=time.monotonic) -> None:
        self.id, self.name, self.input, self.at, self.pending = id, name, input, now(), True

    def done(self, id: str) -> None:
        if id == self.id:
            self.pending = False


REASON_HINTS = {
    "completed": "Model finished without a text summary; consult recorded tool results.",
    "max_turns": "Run reached its turn budget; verify wrap-up persistence in the SDK trace.",
    "timeout": "Run reached its wall-clock budget; verify wrap-up persistence in the SDK trace.",
    "model_error": "Model or API failed after retries; inspect the trace before retrying.",
    "blocking_limit": "Request blocked at the context hard limit; narrow or compress input.",
    "prompt_too_long": "Prompt too long after compaction retries.",
    "image_error": "Model cannot process this multimodal content.",
    "stop_hook_prevented": "Stop hook prevented completion; inspect guard rules.",
    "hook_stopped": "Tool or hook stopped execution; inspect the interception reason.",
    "aborted_streaming": "Run canceled during model streaming.",
    "aborted_tools": "Run canceled during tool execution.",
}


def terminal_reason_hint(reason: str) -> str:
    if not reason:
        return "Context canceled without a terminal event."
    return REASON_HINTS.get(reason, "Unknown terminal reason; update the SDK adapter.")
