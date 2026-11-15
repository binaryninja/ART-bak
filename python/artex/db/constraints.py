"""Exploration-scoped task_constraints CRUD through an injected SQL executor."""
from dataclasses import dataclass
from datetime import datetime
from .core import Executor, Query, int64


class ConstraintReadOverflow(RuntimeError):
    pass


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

    def list_bounded(self, *, max_rows=1000, max_text_bytes=16384,
                     max_origin_bytes=128, max_total_bytes=1048576) -> "list[Constraint]":
        """One-snapshot bounded HTTP projection; overflow fails, never truncates.

        Window totals cover the whole selection before LIMIT. Oversized text is
        replaced with NULL inside SQL, before crossing the executor boundary.
        Existing unbounded list() remains unchanged for explicit trusted callers.
        """
        for value, cap in ((max_rows,10000),(max_text_bytes,1048576),
                           (max_origin_bytes,65536),(max_total_bytes,16777216)):
            if type(value) is not int or not 0<value<=cap:
                raise ValueError('Positive bounded constraint read limits required')
        rows=self.executor.fetch_all(Query("""SELECT id,kind,
CASE WHEN total_count<=$3 AND total_bytes<=$6 AND octet_length(text)<=$4
          AND octet_length(origin)<=$5 THEN text ELSE NULL END,
CASE WHEN total_count<=$3 AND total_bytes<=$6 AND octet_length(text)<=$4
          AND octet_length(origin)<=$5 THEN origin ELSE NULL END,
created_at,total_count
FROM (SELECT id,kind,text,COALESCE(origin,'') AS origin,created_at,
COUNT(*) OVER () AS total_count,
SUM(octet_length(text)+octet_length(COALESCE(origin,''))) OVER () AS total_bytes
FROM task_constraints WHERE exploration_id=$1
ORDER BY (kind='deny'),id LIMIT $2) AS bounded_constraints
ORDER BY (kind='deny'),id""",(self.exploration_id,max_rows+1,max_rows,max_text_bytes,max_origin_bytes,max_total_bytes)))
        if len(rows)>max_rows:raise ConstraintReadOverflow('Constraint view exceeds bounds')
        result=[];size=0
        for row in rows:
            if len(row)!=6 or type(row[5]) is not int or row[5]>max_rows or row[5]!=len(rows):
                raise ConstraintReadOverflow('Incomplete constraint view')
            if not isinstance(row[2],str) or not isinstance(row[3],str):
                raise ConstraintReadOverflow('Constraint view exceeds bounds')
            text_size=len(row[2].encode('utf-8'));origin_size=len(row[3].encode('utf-8'))
            size+=text_size+origin_size
            if text_size>max_text_bytes or origin_size>max_origin_bytes or size>max_total_bytes:
                raise ConstraintReadOverflow('Constraint view exceeds bounds')
            result.append(Constraint(*row[:5]))
        return result

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
