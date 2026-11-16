"""Explicit global successful-skill aggregate read, including cold task summaries.

No HTTP route/default application wiring. One SQL statement gives one database
statement snapshot, not a cross-request snapshot or revocation lock by itself.
"""
from dataclasses import dataclass
from .core import Query
from .skill_usage_read import UsageScope
from .archive_skill_stats import decode_archive_skill_stats
from .skill_stats import merge_skill_stats


class SuccessfulStatsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SuccessfulStatsSelection:
    """Explicit whole-ledger selection; output is never silently truncated."""
    def validate(self):pass


_SQL = """WITH hot AS (
 SELECT skill,COUNT(*) AS calls,COUNT(DISTINCT task_id) AS tasks,
 COALESCE(STRING_AGG(DISTINCT agent_key, ','),'') AS agents,
 MAX(ts) AS last_used
 FROM skill_usage WHERE found GROUP BY skill
), summaries AS (
 SELECT 0 kind,skill ordering,jsonb_build_object('skill_stats',
 jsonb_build_array(jsonb_build_object('skill',skill,'calls',calls,'tasks',tasks,
 'agents',CASE WHEN agents='' THEN '[]'::jsonb ELSE to_jsonb(string_to_array(agents,',')) END,
 'last_used',last_used)))::text raw FROM hot
 UNION ALL
 SELECT 1 kind,archive.id::text ordering,archive.aggregate_stats::text raw
 FROM task_archives archive JOIN tasks task ON task.id=archive.task_id
 WHERE task.archived_at IS NOT NULL
), bounded AS (
 SELECT kind,ordering,raw FROM summaries ORDER BY kind,ordering LIMIT $1
), measured AS (
 SELECT *,sum(octet_length(raw)) OVER () total_bytes FROM bounded
)
SELECT kind,CASE WHEN total_bytes<=$2 THEN raw END,total_bytes>$2
FROM measured ORDER BY kind,ordering"""


class SuccessfulSkillStatsReader:
    def __init__(self, executor, scope, *, authorize_global, current,
                 lifecycle_guard, max_rows=10000, max_bytes=1048576):
        if type(scope) is not UsageScope:
            raise ValueError('Explicit global scope required')
        scope.validate()
        if not callable(getattr(executor, 'fetch_all', None)) or any(not callable(x) for x in
                (authorize_global, current, lifecycle_guard)):
            raise ValueError('Explicit read authority required')
        if type(max_rows) is not int or not 1 <= max_rows <= 100000:
            raise ValueError('Invalid row bound')
        if type(max_bytes) is not int or not 1024 <= max_bytes <= 16777216:
            raise ValueError('Invalid byte bound')
        self.executor, self.scope = executor, scope
        self.authorize_global, self.current, self.guard = authorize_global, current, lifecycle_guard
        self.max_rows, self.max_bytes = max_rows, max_bytes
        self._pins = (executor, scope, authorize_global, current, lifecycle_guard, max_rows, max_bytes)
        self._scope = (scope.workspace, scope.revision)

    def validate(self):
        e,s,a,c,g,r,b = self._pins
        if (self.executor is not e or self.scope is not s or self.authorize_global is not a or
                self.current is not c or self.guard is not g or type(self.max_rows) is not int or
                type(self.max_bytes) is not int or self.max_rows != r or self.max_bytes != b or
                (s.workspace,s.revision) != self._scope):
            raise SuccessfulStatsUnavailable('Read identity changed')
        s.validate()

    def _allow(self, selection, identity, disclosure):
        def pins():
            self.validate()
            if type(selection) is not SuccessfulStatsSelection:
                raise SuccessfulStatsUnavailable('Selection changed')
            selection.validate()
            if identity != ():
                raise SuccessfulStatsUnavailable('Selection changed')
        pins()
        if self._pins[3](self._pins[1]) is not True:
            raise PermissionError('Missing-skill scope expired')
        pins()
        if disclosure is not None:
            disclosure()
        pins()
        if self._pins[3](self._pins[1]) is not True:
            raise PermissionError('Missing-skill scope expired')
        pins()
        # A current-scope callback may retire a grant while observing lifecycle
        # state. Recheck disclosure/global authority AFTER the last such callback.
        # Final authority observers must be cooperative/nonmutating; a finite
        # sequence cannot make arbitrary mutually mutating callbacks atomic.
        if disclosure is not None:
            disclosure()
        pins()
        if self._pins[2](self._pins[1], selection) is not True:
            raise PermissionError('Missing-skill read denied')
        pins()

    def validate_disclosure(self, selection, *, disclosure=None):
        if type(selection) is not SuccessfulStatsSelection:
            raise ValueError('Exact selection required')
        selection.validate()
        identity = ()
        self.validate()
        with self._pins[4]():
            self._allow(selection, identity, disclosure)
        self._allow(selection, identity, disclosure)

    def read(self, selection, *, disclosure=None):
        if type(selection) is not SuccessfulStatsSelection:
            raise ValueError('Exact selection required')
        selection.validate()
        identity = ()
        self.validate()
        with self._pins[4]():
            self._allow(selection, identity, disclosure)
            rows = self._pins[0].fetch_all(Query(_SQL, (self._pins[5]+1,self._pins[6])))
            self._allow(selection, identity, disclosure)
            if type(rows) not in (list,tuple) or len(rows) > self._pins[5]:
                raise SuccessfulStatsUnavailable('Aggregate row bound exceeded')
            live, archived = [], []
            hot_names = set()
            size, stat_rows = 0, 0
            for row in rows:
                if type(row) not in (tuple,list) or len(row) != 3 or type(row[0]) is not int or row[0] not in (0,1) or row[2] is not False or type(row[1]) is not str:
                    raise SuccessfulStatsUnavailable('Invalid bounded aggregate')
                size += len(row[1].encode('utf-8'))
                if size > self._pins[6]:
                    raise SuccessfulStatsUnavailable('Aggregate byte bound exceeded')
                try:projection = decode_archive_skill_stats(row[1], max_bytes=self._pins[6], max_rows=self._pins[5])
                except ValueError:raise SuccessfulStatsUnavailable("Invalid archive aggregate") from None
                stat_rows += len(projection.skill_stats) if projection.skill_stats else len(projection.skills or {})
                if stat_rows > self._pins[5]:
                    raise SuccessfulStatsUnavailable('Aggregate stat bound exceeded')
                if row[0] == 0:
                    if len(projection.skill_stats) != 1 or projection.missing_skill_stats or projection.skills:
                        raise SuccessfulStatsUnavailable('Invalid hot aggregate')
                    name = projection.skill_stats[0].skill
                    if name in hot_names:
                        raise SuccessfulStatsUnavailable('Duplicate hot aggregate')
                    hot_names.add(name)
                    live.extend(projection.skill_stats)
                else:
                    archived.append(projection)
            try:merged = merge_skill_stats(live, archived, missing=False,
                        max_rows=self._pins[5], max_text_bytes=self._pins[6])
            except ValueError:raise SuccessfulStatsUnavailable("Invalid merged aggregate") from None
            out = []
            for row in merged:
                stamp = row.last_used.isoformat() if row.last_used is not None else None
                if stamp is not None:
                    stamp = stamp.replace('+00:00','Z')
                    if row.last_used.microsecond:
                        fraction = f'{row.last_used.microsecond:06d}'
                        stamp = stamp.replace('.'+fraction,'.'+fraction.rstrip('0'),1)
                out.append(dict(skill=row.skill,calls=row.calls,tasks=row.tasks,
                                agents=list(row.agents),last_used=stamp))
            self._allow(selection, identity, disclosure)
        self._allow(selection, identity, disclosure)
        return {'stats':out}
