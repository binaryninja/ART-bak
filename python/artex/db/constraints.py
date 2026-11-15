"""Exploration-scoped task_constraints CRUD through an injected SQL executor."""
from dataclasses import dataclass
from datetime import datetime
from .core import Executor, Query, int64


class ConstraintNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Constraint:
    id: int
    kind: str
    text: str
    origin: str
    created_at: datetime


def _kind(kind: str):
    if kind not in ("allow", "deny"):
        raise ValueError("kind must be allow or deny")


class ConstraintStore:
    def __init__(self, executor: Executor, exploration_id: int):
        self.executor = executor
        self.exploration_id = int64(exploration_id, "exploration_id")

    def list(self) -> list[Constraint]:
        rows = self.executor.fetch_all(Query(
            "SELECT id, kind, text, COALESCE(origin,''), created_at "
            "FROM task_constraints WHERE exploration_id=$1 ORDER BY (kind='deny'), id",
            (self.exploration_id,)))
        return [Constraint(*row) for row in rows]

    def add(self, kind: str, text: str, origin: str = "") -> int:
        _kind(kind)
        row = self.executor.fetch_one(Query(
            "INSERT INTO task_constraints(exploration_id, kind, text, origin) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            (self.exploration_id, kind, text, origin or "system")))
        return int64(row[0], "constraint id")

    def update(self, id: int, kind: str, text: str) -> None:
        _kind(kind)
        affected = self.executor.execute(Query(
            "UPDATE task_constraints SET kind=$1, text=$2, updated_at=now() "
            "WHERE id=$3 AND exploration_id=$4",
            (kind, text, int64(id, "constraint id"), self.exploration_id)))
        if affected == 0:
            raise ConstraintNotFound("constraint does not exist in this exploration")

    def delete(self, id: int) -> None:
        affected = self.executor.execute(Query(
            "DELETE FROM task_constraints WHERE id=$1 AND exploration_id=$2",
            (int64(id, "constraint id"), self.exploration_id)))
        if affected == 0:
            raise ConstraintNotFound("constraint does not exist in this exploration")
