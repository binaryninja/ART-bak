"""Explicit local graph report snapshot and independently authorized global counts.

Source server.getReport: latest 1000 graph findings (not global findings/facts),
plus counts capped at 100000 per asset type. No implicit active task, inheritance,
report model, evidence fetch, mutation or execution permission. Oversized selected
payloads fail rather than transfer an unbounded report. Snapshots across statements
are not claimed atomic against unrelated asset writers.
"""
from dataclasses import dataclass
from .core import Query

class ReportDenied(PermissionError):pass
class ReportUnavailable(RuntimeError):pass
class ReportMissing(LookupError):pass

ASSET_TYPES=('root_domain','ip','subdomain','app','service','endpoint')
@dataclass(frozen=True)
class ReportScope:
    task_id:int
    exploration_id:int
    revision:str
@dataclass(frozen=True)
class ReportSnapshot:
    title:str
    goal:str
    findings:tuple
    asset_counts:tuple

class TaskReportStore:
    def __init__(self,executor,scope,*,current,lifecycle_guard,authorize,global_assets_authorize):
        if type(scope) is not ReportScope or any(type(v) is not int or not 0<v<2**63 for v in (scope.task_id,scope.exploration_id)) or type(scope.revision) is not str or not 1<=len(scope.revision)<=256:
            raise ValueError('Exact local report scope required')
        if any(not callable(f) for f in (current,lifecycle_guard,authorize,global_assets_authorize)):raise ValueError('Separate report and global counts policies required')
        self.executor,self.scope=executor,scope
        self.current,self.guard,self.authorize,self.global_assets_authorize=current,lifecycle_guard,authorize,global_assets_authorize
        self._pins=(executor,scope,current,lifecycle_guard,authorize,global_assets_authorize)
        self._original_pins=self._pins
        self._identity=(scope.task_id,scope.exploration_id,scope.revision)
    def check(self):
        e,s,c,g,a,b=self._original_pins
        if self._pins is not self._original_pins or any(x is not y for x,y in zip((self.executor,self.scope,self.current,self.guard,self.authorize,self.global_assets_authorize),self._pins)) or (s.task_id,s.exploration_id,s.revision)!=self._identity:raise ReportDenied('Report binding changed')
    def validate_read(self):
        self.check();s=self._pins[1]
        for f,args in ((self._pins[2],(s,)),(self._pins[4],(s,)),(self._pins[5],(s,ASSET_TYPES)),(self._pins[4],(s,)),(self._pins[2],(s,))):
            if f(*args) is not True:raise ReportDenied('Report access denied')
            self.check()
    def read(self):
        self.check();e,s,c,g,a,b=self._pins
        self.validate_read()
        with g(s):
            self.validate_read()
            with e.transaction() as tx:
                rows=tx.fetch_all(Query('''SELECT CASE WHEN octet_length(description)<=16384 THEN description ELSE NULL END,
CASE WHEN octet_length(goal)<=16384 THEN goal ELSE NULL END FROM tasks
WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE''',self._identity[:2]))
                self.validate_read()
                if not rows:raise ReportMissing('Local report task unavailable')
                if len(rows)!=1 or len(rows[0])!=2 or any(type(v) is not str or len(v.encode())>16384 for v in rows[0]):raise ReportUnavailable('Report metadata bound exceeded')
                title,goal=rows[0]
                rows=tx.fetch_all(Query('''WITH page AS (
 SELECT id,payload::text AS payload FROM exploration_nodes
 WHERE exploration_id=$1 AND kind='finding' ORDER BY id DESC LIMIT 1000),
sized AS (SELECT id,payload,SUM(octet_length(payload)) OVER() AS bytes FROM page)
SELECT CASE WHEN bytes<=1048576 AND octet_length(payload)<=65536 THEN payload ELSE NULL END
FROM sized ORDER BY id DESC''',(s.exploration_id,)))
                self.validate_read()
                if len(rows)>1000 or any(len(r)!=1 or type(r[0]) is not str or len(r[0].encode())>65536 for r in rows):raise ReportUnavailable('Report findings bound exceeded')
                findings=tuple(r[0] for r in rows)
                if sum(len(v.encode()) for v in findings)>1048576:raise ReportUnavailable('Report aggregate bound exceeded')
                rows=tx.fetch_all(Query('''SELECT kinds.kind,counts.n FROM unnest($1::text[]) AS kinds(kind)
CROSS JOIN LATERAL (SELECT COUNT(*) AS n FROM
 (SELECT 1 FROM assets WHERE type=kinds.kind LIMIT 100000) AS capped) AS counts''',(list(ASSET_TYPES),)))
                self.validate_read()
                if len(rows)!=6 or any(len(r)!=2 or type(r[0]) is not str or r[0] not in ASSET_TYPES or type(r[1]) is not int or not 0<=r[1]<=100000 for r in rows) or len({r[0] for r in rows})!=6:raise ReportUnavailable('Report counts unavailable')
                result=ReportSnapshot(title,goal,findings,tuple((k,v) for k,v in rows if v))
            self.validate_read()
        self.validate_read()
        return result
