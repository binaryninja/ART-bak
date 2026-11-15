"""Asset query/row contracts, not an AssetStore replacement.

Scope membership uses the upstream task/source-task CTE and try_inet SQL
function. Task metadata hydration remains the caller's responsibility.
"""
from copy import deepcopy
import json
from typing import Any, Sequence

from ..asset_dsl import compile_dsl
from .core import Query, int64
from .sql import ASSET_SELECT, SCOPE_TARGET_CTE


def _where(dsl: str, typ: str, task_id: int, in_scope: bool):
    int64(task_id, "task_id")
    if not isinstance(typ, str):
        raise ValueError("type filter must be text")
    scoped = in_scope and task_id > 0
    clause, values = compile_dsl(dsl, base=1 if scoped else 0)
    params = ([task_id] if scoped else []) + values
    # Deliberate correction: Go appends AND to a potentially top-level OR.
    parts = ["(" + clause + ")"]
    if typ:
        params.append(typ)
        parts.append(f"type = ${len(params)}")
    if scoped:
        parts.append("id IN (SELECT id FROM target)")
    elif task_id > 0 and not in_scope:
        params.append(task_id)
        parts.append(f"${len(params)} = ANY(task_ids)")
    prefix = "WITH " + SCOPE_TARGET_CTE + " " if scoped else ""
    return prefix, " AND ".join(parts), params


def asset_query(dsl: str = "", typ: str = "", task_id: int = 0,
                limit: int = 50, offset: int = 0, *, in_scope: bool = False) -> Query:
    """Plan QueryDSL or QueryDSLInScope; nonpositive task id means global."""
    int64(limit, "limit")
    int64(offset, "offset")
    prefix, where, params = _where(dsl, typ, task_id, in_scope)
    params.extend((50 if limit <= 0 else limit, max(0, offset)))
    return Query(prefix + ASSET_SELECT + " WHERE " + where +
                 f" ORDER BY last_seen DESC, id DESC LIMIT ${len(params)-1} OFFSET ${len(params)}",
                 tuple(params))


def asset_count(dsl: str = "", typ: str = "", task_id: int = 0,
                *, in_scope: bool = False) -> Query:
    """Count with the identical filters; scoped count is a Python extension."""
    prefix, where, params = _where(dsl, typ, task_id, in_scope)
    return Query(prefix + "SELECT count(*) FROM assets WHERE " + where, tuple(params))


def assets_by_ids(ids: Sequence[int], *, task_id: int = 0) -> Query:
    """Plan membership-scoped ID lookup. Empty input is explicitly no results."""
    int64(task_id, "task_id")
    ids = tuple(int64(i, "asset id") for i in ids)
    params = ([task_id] if task_id > 0 else []) + list(ids)
    start = 2 if task_id > 0 else 1
    where = ("id IN (" + ",".join(f"${n}" for n in range(start, start + len(ids))) + ")"
             if ids else "FALSE")
    prefix = ""
    if task_id > 0:
        prefix = "WITH " + SCOPE_TARGET_CTE + " "
        where += " AND id IN (SELECT id FROM target)"
    return Query(prefix + ASSET_SELECT + " WHERE " + where +
                 " ORDER BY last_seen DESC, id DESC", tuple(params))


_COLUMNS = ("id type company_id task_ids domain root_domain ip c_segment port icp "
            "bound_domains open_ports record_type record_value bundle_id app_name "
            "category app_description app_icp url service_type service_name favicon_mmh3 "
            "status_code content_length page_title technologies auth method params extra last_seen").split()
_ARRAYS = {"task_ids": int, "bound_domains": str, "open_ports": dict,
           "record_value": str, "technologies": str, "auth": dict, "params": dict}


def decode_asset(row: Sequence[Any]) -> dict[str, Any]:
    """Decode ASSET_SELECT's 32-column row, preserving NULL and empty arrays.

    Unlike Go's ignored json.Unmarshal errors, malformed JSON raises ValueError
    rather than quietly discarding metadata. JSON can be driver-decoded already.
    """
    if len(row) != len(_COLUMNS):
        raise ValueError(f"expected {len(_COLUMNS)} asset columns, received {len(row)}")
    out = dict(zip(_COLUMNS, row))
    for field, item_type in {**_ARRAYS, "extra": dict}.items():
        raw = out[field]
        value = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else deepcopy(raw)
        if value is not None:
            if field == "extra":
                valid = isinstance(value, dict)
            else:
                valid = isinstance(value, list) and all(type(x) is item_type for x in value)
            if not valid:
                raise ValueError(f"invalid JSON shape for asset {field}")
        out[field] = value
    return out
