"""Decode only the inert skill dimensions of a supplied archive summary.

No archive selection, database read, file loading, or disclosure authority is
provided here. The whole JSON document is bounded before projection, including
unknown dimensions. Duplicate keys fail closed instead of last-key-wins parsing.
"""
import json
import re
from datetime import datetime, timezone
from .skill_stats import ArchivedSkillStats, SkillStat, merge_skill_stats

_STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z')


def decode_archive_skill_stats(raw, *, max_bytes=1048576, max_rows=10000,
                               max_depth=16, max_nodes=100000):
    """Return an owned projection of v1 counts and modern skill arrays.

    Missing/null arrays or maps are empty, matching Go JSON zero values. Unknown
    dimensions are ignored after bounded structural validation. Submicrosecond
    timestamps are refused rather than silently truncated. No provenance implied.
    """
    for value, ceiling in ((max_bytes, 16777216), (max_rows, 100000),
                           (max_depth, 64), (max_nodes, 1000000)):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise ValueError('Invalid summary limits')
    if type(raw) not in (str, bytes):
        raise ValueError('Raw UTF-8 summary required')
    if len(raw) > max_bytes:
        raise ValueError('Summary byte limit exceeded')
    if type(raw) is bytes:
        raw = raw.decode('utf-8', errors='strict')
    if len(raw.encode('utf-8')) > max_bytes:
        raise ValueError('Summary byte limit exceeded')
    # Bound nesting before recursive JSON decoding. Quotes and escapes make
    # braces inside strings inert; the decoder handles remaining syntax errors.
    depth = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > max_depth:
                raise ValueError('Summary depth exceeded')
        elif char in ']}':
            depth -= 1
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate summary key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Nonfinite JSON value')
    document = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    stack = [document]
    nodes = 0
    while stack:
        value = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValueError('Summary node limit exceeded')
        if type(value) is str:
            value.encode('utf-8')  # rejects escaped lone surrogates
        elif type(value) is dict:
            stack.extend(value.keys())
            stack.extend(value.values())
        elif type(value) is list:
            stack.extend(value)
        elif type(value) is float:
            # JSON numeric overflow (e.g. 1e999) also yields infinity.
            import math
            if not math.isfinite(value):
                raise ValueError('Nonfinite JSON value')
    if document is None:
        document = {}
    if type(document) is not dict:
        raise ValueError('Summary object required')
    row_count = 0
    def count(value):
        if type(value) is not int or not 0 <= value < 2**63:
            raise ValueError('Invalid skill count')
        return value
    def consume_row():
        nonlocal row_count
        row_count += 1
        if row_count > max_rows:
            raise ValueError('Summary row limit exceeded')
    def rows(key):
        selected = document.get(key)
        if selected is None:
            return ()
        if type(selected) is not list:
            raise ValueError('Skill stats array required')
        result = []
        for row in selected:
            consume_row()
            if type(row) is not dict:
                raise ValueError('Skill stat object required')
            skill = row.get('skill', '')
            agents = row.get('agents')
            if agents is None:
                agents = []
            if type(skill) is not str or not skill or type(agents) is not list or any(type(a) is not str for a in agents):
                raise ValueError('Invalid skill dimensions')
            stamp = row.get('last_used')
            if stamp is not None:
                if type(stamp) is not str or not _STAMP.fullmatch(stamp):
                    raise ValueError('Invalid summary timestamp')
                # fromisoformat accepts out-of-range offset minutes by
                # normalization; RFC3339 requires 00..59 and hours 00..23.
                if stamp[-1] != 'Z' and (int(stamp[-5:-3]) > 23 or int(stamp[-2:]) > 59):
                    raise ValueError('Invalid summary timestamp offset')
                stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                try:
                    stamp.astimezone(timezone.utc)
                except (OverflowError, ValueError):
                    raise ValueError('Summary timestamp outside UTC range') from None
            result.append(SkillStat(skill, count(row.get('calls', 0)),
                                    count(row.get('tasks', 0)), tuple(agents), stamp))
        return tuple(result)
    legacy = document.get('skills')
    if legacy is not None:
        if type(legacy) is not dict:
            raise ValueError('Legacy skill map required')
        legacy = dict(legacy)
        for name, calls in legacy.items():
            consume_row()
            if not name:
                raise ValueError('Empty legacy skill name')
            count(calls)
    result = ArchivedSkillStats(legacy, rows('skill_stats'), rows('missing_skill_stats'))
    # Reuse the pure projection validation for both dimensions. Decode validates
    # every field even when a later merge would ignore a legacy fallback map.
    merge_skill_stats([], [result], max_rows=max_rows, max_text_bytes=max_bytes)
    merge_skill_stats([], [result], missing=True, max_rows=max_rows, max_text_bytes=max_bytes)
    return result
