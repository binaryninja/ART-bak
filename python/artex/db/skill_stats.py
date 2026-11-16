"""Bounded, inert skill-stat merging from db/task_archive_aggregate_stats.go.

This is a projection primitive, not an authorized reader. Callers must bound and
validate archive decoding before passing these objects, and authorize disclosure.
No filesystem, database, skill execution or implicit archive loading occurs.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SkillStat:
    skill: str
    calls: int = 0
    tasks: int = 0
    agents: tuple[str, ...] = ()
    last_used: datetime | None = None


@dataclass(frozen=True)
class ArchivedSkillStats:
    skills: dict[str, int] | None = None  # legacy v1 counts
    skill_stats: tuple[SkillStat, ...] = ()
    missing_skill_stats: tuple[SkillStat, ...] = ()


def merge_skill_stats(live, archived, *, missing=False, max_rows=10000,
                      max_text_bytes=1048576):
    """Return fresh sorted SkillStats, merging archives with source semantics.

    Miss names are inert model-provided text, not validated filesystem names.
    Cold task counts are added, not globally deduplicated. Legacy `skills` maps
    contribute one task per entry only to successful stats, never missing stats.
    Aware timestamps, finite counts and exact plain containers are mandatory.
    Limits cover all input rows/text including duplicates, not only the output.
    """
    if type(missing) is not bool or type(max_rows) is not int or not 1 <= max_rows <= 100000:
        raise ValueError('Invalid aggregate limits')
    if type(max_text_bytes) is not int or not 1 <= max_text_bytes <= 16777216:
        raise ValueError('Invalid aggregate limits')
    if type(live) not in (tuple, list) or type(archived) not in (tuple, list):
        raise ValueError('Plain aggregate containers required')
    if len(archived) > max_rows:
        raise ValueError('Too many archives')
    rows = 0
    text_bytes = 0
    by_name = {}

    def consume(row, *, replace=False):
        nonlocal rows, text_bytes
        rows += 1
        if rows > max_rows or type(row) is not SkillStat:
            raise ValueError('Invalid aggregate row')
        if type(row.skill) is not str or not row.skill or type(row.agents) is not tuple:
            raise ValueError('Invalid aggregate text')
        if any(type(n) is not int or not 0 <= n < 2**63 for n in (row.calls, row.tasks)):
            raise ValueError('Invalid aggregate count')
        if row.last_used is not None and (type(row.last_used) is not datetime or
                type(row.last_used.tzinfo) not in (timezone, ZoneInfo)):
            raise ValueError('Aware aggregate timestamp required')
        if row.last_used is not None:
            try:
                row.last_used.astimezone(timezone.utc)
            except (OverflowError, ValueError):
                raise ValueError('Aggregate timestamp outside UTC range') from None
        if len(row.agents) > max_rows or any(type(a) is not str for a in row.agents):
            raise ValueError('Invalid aggregate agents')
        text_bytes += len(row.skill.encode('utf-8')) + sum(len(a.encode('utf-8')) for a in row.agents)
        if text_bytes > max_text_bytes:
            raise ValueError('Aggregate text limit exceeded')
        if replace:
            # Source initializes the live map by assignment. Live SQL is grouped.
            by_name[row.skill] = SkillStat(row.skill, row.calls, row.tasks, tuple(row.agents), row.last_used)
            return
        current = by_name.get(row.skill, SkillStat(row.skill))
        calls, tasks = current.calls + row.calls, current.tasks + row.tasks
        if calls >= 2**63 or tasks >= 2**63:
            raise ValueError('Aggregate count overflow')
        agents = tuple(dict.fromkeys(a for a in current.agents + row.agents if a))
        last = current.last_used
        # Same-zone datetime comparison otherwise uses wall time, including
        # ambiguous fall-back folds. Compare instants but preserve the value.
        if row.last_used is not None and (last is None or
                row.last_used.astimezone(timezone.utc) > last.astimezone(timezone.utc)):
            last = row.last_used
        by_name[row.skill] = SkillStat(row.skill, calls, tasks, agents, last)

    for row in live:
        consume(row, replace=True)
    for item in archived:
        if type(item) is not ArchivedSkillStats or type(item.skill_stats) is not tuple or type(item.missing_skill_stats) is not tuple:
            raise ValueError('Invalid archive projection')
        selected = item.missing_skill_stats if missing else item.skill_stats
        if not missing and not selected:
            legacy = item.skills
            if legacy is not None and type(legacy) is not dict:
                raise ValueError('Invalid legacy aggregate')
            if legacy is not None:
                for name, calls in legacy.items():
                    consume(SkillStat(name, calls, 1))
                continue
        for row in selected:
            consume(row)
    return sorted(by_name.values(), key=lambda row: (-row.calls, row.skill))
