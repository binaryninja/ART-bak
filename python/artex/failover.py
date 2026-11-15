"""Pure error classification port of llmpool/pool.go.

This does not retry requests or call models. A streaming adapter must separately
ensure failover happens BEFORE any event reaches a caller. Context cancellation
is distinct from transport timeouts: only the former suppresses failover.
"""
import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
import re


class ContextCancelledError(Exception):
    """Task context cancelled, not a provider/transport failure."""


class ContextDeadlineExceededError(Exception):
    """Task context deadline expired, not a provider socket timeout."""


# Go regexp \d matches ASCII digits; Python's default also matches Unicode.
# Explicit ASCII digits preserve Go behavior (including a 3-digit prefix of 4).
_STATUS = re.compile(r"status ([0-9]{3})")
_CONTEXT_ERRORS = (ContextCancelledError, ContextDeadlineExceededError,
                   asyncio.CancelledError, FutureCancelledError)


def status_of(error: BaseException | str) -> int:
    """Extract first, case-sensitive SDK 'status NNN' occurrence, or zero."""
    match = _STATUS.search(str(error))
    return int(match.group(1)) if match else 0


def _context_failure(error: BaseException | str) -> bool:
    """Follow explicit Python wrapping (__cause__), analogous to errors.Is."""
    seen: set[int] = set()
    current = error
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _CONTEXT_ERRORS):
            return True
        current = current.__cause__
    return False


def should_failover(error: BaseException | str | None, *, context_cancelled: bool = False,
                    context_deadline_exceeded: bool = False) -> bool:
    if error is None or context_cancelled or context_deadline_exceeded or _context_failure(error):
        return False
    code = status_of(error)
    return code == 0 or code in (401, 402, 403, 404, 408, 429) or code >= 500


def is_hard_failure(error: BaseException | str) -> bool:
    return status_of(error) in (401, 402, 403, 404)


def trim_error(error: BaseException | str) -> str:
    """Replace newlines; strip whitespace; truncate to 300 UTF-8 bytes.

    Go slices bytes and can produce invalid UTF-8 at the boundary. This port
    drops an incomplete final code point instead, always returning valid text.
    Python strip also recognizes U+001C..U+001F; use Go's Unicode White_Space
    set explicitly to preserve upstream trimming semantics.
    """
    whitespace = '\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'
    text = str(error).replace('\n', ' ').strip(whitespace)
    raw = text.encode('utf-8')
    if len(raw) > 300:
        return raw[:300].decode('utf-8', errors='ignore') + '…'
    return text
