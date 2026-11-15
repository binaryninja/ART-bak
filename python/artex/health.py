"""Thread-safe circuit-breaker registry ported from llmpool/health.go.

This is the health registry, not an LLM provider or failover transport.
Timestamps use UTC Unix seconds, allowing persistence across process restarts.
"""
from dataclasses import dataclass, replace
from threading import Lock
from time import time
from typing import Callable


@dataclass
class State:
    fails: int = 0
    trips: int = 0
    open_until: float = 0.0
    last_error: str = ""
    last_at: float = 0.0

    def is_open(self, now: float | None = None) -> bool:
        return (time() if now is None else now) < self.open_until


class Registry:
    """Shared health state; callbacks receive independent snapshots off-lock."""

    def __init__(self, persist: Callable[[int, State], None] | None = None,
                 forget: Callable[[int], None] | None = None,
                 clock: Callable[[], float] = time):
        self._lock = Lock()
        self._states: dict[int, State] = {}
        self._revision = 0
        self._persist, self._forget, self._clock = persist, forget, clock
        self._soft_trip, self._cooldown = 0, 0.0

    def set_policy(self, soft_trip: int = 0, cooldown: float = 0) -> None:
        """Zero selects defaults; negative soft_trip disables transient trips."""
        with self._lock:
            self._soft_trip, self._cooldown = soft_trip, cooldown

    def restore(self, profile_id: int, state: State) -> None:
        with self._lock:
            self._states[profile_id] = replace(state)
            self._revision += 1

    def get(self, profile_id: int) -> State:
        with self._lock:
            return replace(self._states.get(profile_id, State()))

    def is_open(self, profile_id: int) -> bool:
        return self.get(profile_id).is_open(self._clock())

    def pass_(self, profile_id: int) -> None:
        with self._lock:
            state = self._states.get(profile_id)
            if state is None or (state.fails == state.trips == 0 and state.open_until == 0):
                return
            del self._states[profile_id]
            self._revision += 1
        if self._forget is not None:
            self._forget(profile_id)

    def trip(self, profile_id: int, error: str, hard: bool = False) -> bool:
        with self._lock:
            state = self._states.setdefault(profile_id, State())
            state.fails += 1
            state.last_error, state.last_at = error, self._clock()
            threshold = 3 if self._soft_trip == 0 else max(self._soft_trip, 0)
            tripped = hard or (threshold > 0 and state.fails >= threshold)
            if tripped:
                state.trips += 1
                delay = self._cooldown if self._cooldown > 0 else (60, 300, 1800)[min(state.trips - 1, 2)]
                state.open_until = self._clock() + delay
                state.fails = 0
            self._revision += 1
            snapshot = replace(state)
        if self._persist is not None:
            self._persist(profile_id, snapshot)
        return tripped

    def reset(self, profile_id: int) -> None:
        with self._lock:
            self._states.pop(profile_id, None)
            self._revision += 1
        if self._forget is not None:
            self._forget(profile_id)

    def snapshot(self) -> dict[int, State]:
        with self._lock:
            return {key: replace(value) for key, value in self._states.items()}

    def inventory(self, limit: int) -> tuple[int, tuple[int, ...]]:
        """Bounded IDs only, including orphan health entries; never copies errors."""
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("Invalid inventory bound")
        with self._lock:
            if len(self._states) > limit:
                raise ValueError("Health inventory exceeds bound")
            if any(type(i) is not int or not 0 < i < 2**63 for i in self._states):
                raise ValueError("Invalid health inventory")
            return self._revision, tuple(sorted(self._states))

    def reset_inventory(self, expected: tuple[int, tuple[int, ...]], limit: int, *, current: Callable[[], None]) -> None:
        """CAS-clear one approved inventory, then persist off-lock.

        Callback failure or concurrent mutation after the clear is uncertain:
        callers must reconcile, never retry automatically. No new IDs are reset.
        This deliberately makes memory removal atomic rather than Go's loop.
        """
        if not callable(current):
            raise ValueError("Explicit current fence required")
        forget = self._forget
        current()
        revision, ids = self.inventory(limit)
        if expected != (revision, ids):
            raise ValueError("Health inventory changed")
        with self._lock:
            if self._forget is not forget or self._revision != revision or tuple(sorted(self._states)) != ids:
                raise ValueError("Health inventory changed")
            self._states.clear()
            self._revision += 1
            cleared_revision = self._revision
        def check():
            current()
            with self._lock:
                if self._forget is not forget or self._revision != cleared_revision:
                    raise RuntimeError("Health inventory changed during reset")
        for profile_id in ids:
            check()
            if forget is not None:
                forget(profile_id)
            check()
        check()
