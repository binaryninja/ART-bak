"""Source activity-based token summaries, not provider billing estimates.

Maps exploration.go TokenDailyAll/TokenStatsByWorker/Session/TokenTotal and
conversation.go ConversationTokenSummaries. No llm_records/llm_usage substitution:
only completed result activity rows count. These routes have NO price/cost fields;
unknown pricing is not zero cost and no float estimates are invented. Integer
PostgreSQL sums retain precision. Daily cutoff is rolling DB NOW()-days, grouped
UTC calendar day; conversation creation text retains source DB session timezone.
Archive reconciliation unavailable: reject archives in relevant scope. Explicit
whole-workspace authority needed for global daily/conversation routes; a task
scope cannot silently become global. No inherited accounting or active fallback.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from decimal import Decimal
from .core import Query,int64

class UsageDenied(PermissionError):pass
class UsageUnavailable(RuntimeError):pass
@dataclass(frozen=True)
class UsageScope:
    workspace:str
    task_id:int|None=None
    exploration_id:int|None=None
FIELDS=('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')
def _integer(value):
    if isinstance(value,Decimal) and value.is_finite() and value==value.to_integral_value():value=int(value)
    if type(value) is not int or not 0<=value<2**63:raise UsageUnavailable('Invalid token aggregate')
    return value

class TokenUsageStore:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard,max_rows=10000,max_bytes=1048576):
        if type(scope) is not UsageScope or not isinstance(scope.workspace,str) or not scope.workspace or len(scope.workspace)>256:raise ValueError('Explicit usage scope required')
        if (scope.task_id is None)!=(scope.exploration_id is None):raise ValueError('Exact task exploration required')
        for v in (scope.task_id,scope.exploration_id):
            if v is not None and int64(v,'usage identity')<=0:raise ValueError('Positive usage identity required')
        if any(not callable(v) for v in (authorize,current,lifecycle_guard)):raise ValueError('Explicit usage authority required')
        if type(max_rows) is not int or not 1<=max_rows<=10000 or type(max_bytes) is not int or not 1024<=max_bytes<=16777216:raise ValueError('Invalid usage bounds')
        self.executor,self.scope=executor,scope;self._executor,self._scope=executor,scope
        self.authorize,self.current,self.guard=authorize,current,lifecycle_guard;self.max_rows,self.max_bytes=max_rows,max_bytes;self._guard=lifecycle_guard
    def _check(self):
        if self.executor is not self._executor or self.scope is not self._scope or self.guard is not self._guard:raise UsageDenied('Usage binding changed')
        if self.current(self._scope) is not True:raise UsageDenied('Usage authority stale')
        if self.executor is not self._executor or self.scope is not self._scope or self.guard is not self._guard:raise UsageDenied('Usage binding changed')
    def _validate_read_locked(self,op):
        self._check()
        if self.authorize(self._scope,op) is not True:raise UsageDenied('Usage read denied')
        self._check()
    def validate_read(self,op):
        with self._guard(self._scope):
            self._validate_read_locked(op)
    @contextmanager
    def _read(self,op):
        policies=(self.authorize,self.current,)
        def check():
            if any(a is not b for a,b in zip((self.authorize,self.current,),policies)):raise UsageDenied()
            self._check()
            if any(a is not b for a,b in zip((self.authorize,self.current,),policies)):raise UsageDenied()
        with self.guard(self._scope):
            check()
            if self.authorize(self._scope,op) is not True:raise UsageDenied('Usage read denied')
            check()
            with self._executor.transaction() as tx:
                if self._scope.task_id is not None:
                    rows=tx.fetch_all(Query('SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE',(self._scope.task_id,self._scope.exploration_id)))
                    if rows!=[(self._scope.task_id,)]:raise UsageUnavailable('Live usage parent unavailable')
                check();yield tx;self._validate_read_locked(op);check()
            self._validate_read_locked(op);check()
    def _rows(self,rows):
        if len(rows)>self.max_rows:raise UsageUnavailable('Usage row bound exceeded')
        return rows
    def daily(self,days=30):
        if self._scope.task_id is not None:raise UsageDenied('Global daily scope required')
        if type(days) is not int:raise ValueError('Integer days required')
        days=30 if days<=0 else days
        if days>3660:raise ValueError('Date range exceeds bound')
        with self._read('daily') as tx:
            rows=tx.fetch_all(Query("""WITH archive_state AS (SELECT EXISTS(SELECT 1 FROM task_archives) AS blocked), grouped AS (
SELECT TO_CHAR(DATE_TRUNC('day',created_at AT TIME ZONE 'UTC'),'YYYY-MM-DD') AS day,
COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens
FROM activity WHERE kind='result' AND created_at>=NOW()-($1*INTERVAL '1 day') GROUP BY day)
SELECT day,input_tokens,output_tokens,cache_read_tokens FROM grouped
WHERE NOT (SELECT blocked FROM archive_state)
UNION ALL SELECT NULL::text,0::bigint,0::bigint,0::bigint FROM archive_state WHERE blocked
ORDER BY day LIMIT $2""",(days,self.max_rows+1)))
            if any(r[0] is None for r in rows):raise UsageUnavailable('Archived daily accounting unavailable')
            return [dict(day=r[0],**dict(zip(FIELDS[:3],map(_integer,r[1:])))) for r in self._rows(rows)]
    def conversations(self):
        if self._scope.task_id is not None:raise UsageDenied('Global conversation scope required')
        with self._read('conversations') as tx:
            rows=tx.fetch_all(Query("""SELECT c.llm_profile_id,c.created_at::text,
COALESCE(SUM(ca.input_tokens),0),COALESCE(SUM(ca.output_tokens),0),COALESCE(SUM(ca.cache_read_tokens),0),COALESCE(SUM(ca.cache_write_tokens),0)
FROM conversations c LEFT JOIN conversation_activities ca ON ca.conversation_id=c.id AND ca.kind='result'
GROUP BY c.id ORDER BY c.id LIMIT $1""",(self.max_rows+1,)))
            return [dict(llm_profile_id=r[0],created_at=r[1],**dict(zip(FIELDS,map(_integer,r[2:])))) for r in self._rows(rows)]
    def exploration(self):
        if self._scope.task_id is None:raise UsageDenied('Exact task scope required')
        with self._read('exploration') as tx:
            sums=','.join('COALESCE(SUM('+name+'),0) AS '+name for name in FIELDS)
            workers=tx.fetch_all(Query("WITH grouped AS (SELECT COALESCE(worker,'') AS worker,"+sums+" FROM activity WHERE exploration_id=$1 AND kind='result' GROUP BY worker) SELECT CASE WHEN sum(octet_length(worker)) OVER ()<=$2 THEN worker ELSE NULL END,"+','.join(FIELDS)+" FROM grouped ORDER BY worker LIMIT $3",(self._scope.exploration_id,self.max_bytes,self.max_rows+1)))
            sessions=tx.fetch_all(Query("""SELECT CASE WHEN COALESCE(a.worker,'')='mainagent' THEN 'main:'||COALESCE(a.main_seg,0)::text
WHEN COALESCE(a.worker,'')='planner' THEN 'plan' WHEN n.id IS NOT NULL THEN 'intent:'||n.id::text ELSE '' END AS session_key,
"""+','.join('COALESCE(SUM(a.'+name+'),0)' for name in FIELDS)+""" FROM activity a LEFT JOIN exploration_nodes n
ON n.id=a.node_id AND n.exploration_id=a.exploration_id AND n.kind='intent'
WHERE a.exploration_id=$1 AND a.kind='result' AND (COALESCE(a.worker,'') IN ('mainagent','planner') OR n.id IS NOT NULL)
GROUP BY session_key ORDER BY session_key LIMIT $2""",(self._scope.exploration_id,self.max_rows+1)))
            total=tx.fetch_all(Query('SELECT '+sums+" FROM activity WHERE exploration_id=$1 AND kind='result'",(self._scope.exploration_id,)))
            if len(total)!=1 or any(row[0] is None for row in workers):raise UsageUnavailable('Usage aggregate unavailable')
            def entries(rows,key):return [dict(**{key:r[0]},**dict(zip(FIELDS,map(_integer,r[1:])))) for r in self._rows(rows)]
            return dict(workers=entries(workers,'worker'),sessions=entries(sessions,'session'),total=dict(zip(FIELDS,map(_integer,total[0]))))
