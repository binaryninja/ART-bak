"""Audit/guard hook port. Does not execute tools or implement an interceptor.

As upstream, an absent interceptor is audit-only, NOT a security boundary.
Production integration must supply database rule matching and human approval.
"""
from dataclasses import dataclass
import json
import re
from threading import Lock
import time
from typing import Any, Protocol


@dataclass(frozen=True)
class AuditEntry:
    ts: int
    tool: str
    action: str
    reason: str = ""
    command: str = ""


@dataclass(frozen=True)
class Result:
    decision: str = ""
    message: str = ""


@dataclass(frozen=True)
class Decision:
    action: str
    message: str = ""


class Interceptor(Protocol):
    """Adapter contract; context carries cancellation and conversation identity."""
    def is_tool_enabled(self, tool: str) -> bool: ...
    def match(self, tool: str, tool_input: Any) -> tuple[Decision, bool]: ...
    def judge(self, context: Any, tool: str, tool_input: Any) -> tuple[Decision, bool]: ...
    def log(self, context: Any, decision: Decision, tool: str, tool_input: Any, status: str) -> None: ...
    def handle_ask(self, context: Any, decision: Decision, tool: str, tool_input: Any) -> bool: ...


def system_block_message(reason: str) -> str:
    return ("[ARTEX platform policy, not a target defense] The platform blocked "
            f"this call. Reason: {reason}. This operation is prohibited.")


_BLOCKED = re.compile(r"\b(403|forbidden|waf|blocked|rate.?limit|429|captcha|denied)\b", re.I | re.ASCII)


class Guard:
    def __init__(self, interceptor: Interceptor | None = None):
        self.interceptor = interceptor
        self._lock = Lock()
        self._audit: list[AuditEntry] = []
        self._attributions: dict[str, int] = {}

    def _record(self, tool: str, action: str, reason: str = "", command: str = "") -> None:
        with self._lock:
            self._audit.append(AuditEntry(int(time.time()), tool, action, reason, command))
            self._audit = self._audit[-2000:]

    def _block(self, tool: str, reason: str) -> Result:
        self._record(tool, "block", reason)
        return Result("block", reason)

    def pre_tool_use(self, tool: str, tool_input: Any, context: Any = None,
                     *, cancelled: bool = False) -> Result:
        """Evaluate only; callers MUST honor a block before any side effect.

        The adapter must also monitor cancellation while handle_ask is waiting.
        Full, unchanged tool_input is passed to every interceptor method.
        """
        parsed = tool_input
        if isinstance(parsed, (str, bytes, bytearray)):
            try:
                parsed = json.loads(parsed)
            except (ValueError, UnicodeError):
                parsed = {}
        key = "command" if tool in ("Bash", "shell_open") else "text" if tool == "shell_send" else None
        command = parsed.get(key, "") if key and isinstance(parsed, dict) else ""
        self._record(tool, "allow", command=command if isinstance(command, str) else "")
        ic = self.interceptor
        if ic is None or not ic.is_tool_enabled(tool):
            return Result()
        decision, matched = ic.match(tool, tool_input)
        if not matched:
            decision, judged = ic.judge(context, tool, tool_input)
            if not judged:
                return Result()
        if decision.action == "deny":
            ic.log(context, decision, tool, tool_input, "denied")
            return self._block(tool, system_block_message(decision.message))
        if decision.action == "allow":
            ic.log(context, decision, tool, tool_input, "allowed")
        elif decision.action == "ask":
            if cancelled:
                return self._block(tool, system_block_message("Work cancelled; platform policy prevented execution"))
            if not ic.handle_ask(context, decision, tool, tool_input):
                return self._block(tool, system_block_message("Human approval not granted (denied or timed out)"))
        return Result()

    def post_tool_use(self, tool: str, result: str | bytes, is_error: bool = False) -> Result:
        if tool == "Bash":
            if isinstance(result, bytes):
                result = result.decode("utf-8", errors="replace")
            category = "blocked" if _BLOCKED.search(result) else "error" if is_error else "ok"
            with self._lock:
                self._attributions[category] = self._attributions.get(category, 0) + 1
        return Result()

    def attributions(self) -> dict[str, int]:
        with self._lock:
            return dict(self._attributions)

    def audit(self) -> list[AuditEntry]:
        with self._lock:
            return list(self._audit)
