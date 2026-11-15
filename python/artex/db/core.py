"""Driver-neutral contracts. Dollar placeholders are PostgreSQL positional binds.

An adapter must bind params, never interpolate them. This is not Python DB-API's
percent-placeholder dialect and is not a connection/transaction implementation.
"""
from dataclasses import dataclass
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class Query:
    sql: str
    params: tuple[Any, ...] = ()


class Executor(Protocol):
    def fetch_all(self, query: Query) -> Sequence[Sequence[Any]]: ...
    def fetch_one(self, query: Query) -> Sequence[Any]: ...
    def execute(self, query: Query) -> int:
        """Return affected row count; propagate database errors."""
        ...


def int64(value: int, name: str) -> int:
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise ValueError(f"{name} must be a signed 64-bit integer")
    return value
