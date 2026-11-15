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
        self._persist, self._forget, self._clock = persist, forget, clock
        self._soft_trip, self._cooldown = 0, 0.0

    def set_policy(self, soft_trip: int = 0, cooldown: float = 0) -> None:
        """Zero selects defaults; negative soft_trip disables transient trips."""
        with self._lock:
            self._soft_trip, self._cooldown = soft_trip, cooldown

    def restore(self, profile_id: int, state: State) -> None:
        with self._lock:
            self._states[profile_id] = replace(state)

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
            snapshot = replace(state)
        if self._persist is not None:
            self._persist(profile_id, snapshot)
        return tripped

    def reset(self, profile_id: int) -> None:
        with self._lock:
            self._states.pop(profile_id, None)
        if self._forget is not None:
            self._forget(profile_id)

    def snapshot(self) -> dict[int, State]:
        with self._lock:
            return {key: replace(value) for key, value in self._states.items()}
