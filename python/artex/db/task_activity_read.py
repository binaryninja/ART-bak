"""Bounded local task activity reads; no inherited/archive transcript authority.

Source db/exploration.go ActivityList/ActivityPage/CurrentMainSeg and ActivityDTO.
One statement captures local task max sequence, session page and byte guards.
Exact local intent membership is required; inherited sessions need a separate
terminal-source authorization adapter and are deliberately unsupported here.
Raw summaries, metadata and detail require independent grants. No writes/capture.
"""
from dataclasses import dataclass
from copy import deepcopy
import json,re
from .core import Query,int64

class ActivityDenied(RuntimeError):pass
class ActivityUnavailable(RuntimeError):pass
class ActivityMissing(LookupError):pass

@dataclass(frozen=True)
class ActivityReadScope:
    task_id:int
    exploration_id:int
    revision:str

@dataclass(frozen=True)
class ActivitySelection:
    action:str
    since:int=0
    before:int=0
    limit:int=200
    intent:int|None=None
    session:str=''
    seq:int=0

def session_filter(session):
    if session in ('','main'):return 'main',None
    if session=='plan':return 'plan',None
    m=re.fullmatch(r'(main|intent):([0-9]{1,19})',session)
    if not m:raise ValueError('Invalid activity session')
    n=int(m[2])
    if (m[1]=='main' and n>=2**31) or (m[1]=='intent' and not 0<n<2**63):raise ValueError('Invalid activity session')
    return m[1],n

# Strip only optional DTO fields. Metadata is appended afterward to preserve
# nested/null/scalar JSON values; only our private top-level receipt key is hidden.
_PAYLOAD="""(jsonb_strip_nulls(jsonb_build_object('seq',a.id,'intent_id',a.node_id::text,
'worker',COALESCE(a.worker,''),'kind',COALESCE(a.kind,''),'tool',NULLIF(a.tool,''),
'tool_use_id',NULLIF(a.tool_use_id,''),'is_error',a.is_error,'summary',COALESCE(a.summary,''),
'ts',to_char(a.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
'input_tokens',a.input_tokens,'output_tokens',a.output_tokens,'cache_read_tokens',a.cache_read_tokens,
'cache_write_tokens',a.cache_write_tokens,'main_seg',a.main_seg)) ||
CASE WHEN a.metadata IS NULL THEN '{}'::jsonb
ELSE jsonb_build_object('metadata',CASE WHEN jsonb_typeof(a.metadata)='object'
THEN a.metadata - '_artex_projection' ELSE a.metadata END) END)"""

class TaskActivityReadStore:
    def __init__(self,executor,scope,*,current,lifecycle_guard,authorize,raw_authorize,max_bytes=1048576,max_rows=500):
        if type(scope) is not ActivityReadScope or any(int64(v,'identity')<=0 for v in (scope.task_id,scope.exploration_id)) or not isinstance(scope.revision,str) or not scope.revision:raise ValueError('Exact read scope required')
        if any(not callable(f) for f in (current,lifecycle_guard,authorize,raw_authorize)):raise ValueError('Explicit read policies required')
        if type(max_bytes) is not int or not 1<=max_bytes<=1048576 or type(max_rows) is not int or not 1<=max_rows<=500:raise ValueError('Invalid read bounds')
        self.executor,self.scope=executor,scope
        self.current,self.guard,self.authorize,self.raw_authorize=current,lifecycle_guard,authorize,raw_authorize
        self.max_bytes,self.max_rows=max_bytes,max_rows
        self._fixed=(executor,scope,lifecycle_guard,max_bytes,max_rows)
        self._initial_policies=(current,authorize,raw_authorize)
    def pins(self):return self.current,self.authorize,self.raw_authorize
    def check(self,pins):
        e,s,g,b,n=self._fixed
        if self.executor is not e or self.scope is not s or self.guard is not g or (self.max_bytes,self.max_rows)!=(b,n) or any(x is not y for x,y in zip(self.pins(),pins)):raise ActivityUnavailable('Activity read binding changed')
        if pins[0](s) is not True:raise ActivityDenied('Activity scope unavailable')
        if self.executor is not e or self.scope is not s or self.guard is not g or (self.max_bytes,self.max_rows)!=(b,n) or any(x is not y for x,y in zip(self.pins(),pins)):raise ActivityUnavailable('Activity read binding changed')
    def validate_read(self,selection,pins=None):
        pins=self.pins() if pins is None else pins
        self.check(pins)
        for fn in pins[1:]:
            if fn(self.scope,selection) is not True:raise ActivityDenied('Activity read denied')
            self.check(pins)
    def read(self,selection):
        if type(selection) is not ActivitySelection or selection.action not in ('list','history','detail'):raise ValueError('Explicit selection required')
        for v in (selection.since,selection.before,selection.seq):
            if int64(v,'cursor')<0:raise ValueError('Invalid cursor')
        if type(selection.limit) is not int or not 1<=selection.limit<=self.max_rows:raise ValueError('Invalid limit')
        mode,arg=session_filter(selection.session) if selection.action=='history' else ('all',None)
        intent=arg if mode=='intent' else selection.intent
        if intent is not None and int64(intent,'intent')<=0:raise ValueError('Invalid intent')
        pins=self.pins();scope=self.scope
        with self._fixed[2](scope):
            self.validate_read(selection,pins)
            with self._fixed[0].transaction() as tx:
                parent=tx.fetch_all(Query("SELECT exploration_id FROM tasks WHERE id=$1 AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE",(scope.task_id,)))
                if parent!=[(scope.exploration_id,)]:raise ActivityMissing('Local task activity unavailable')
                if intent is not None:
                    found=tx.fetch_all(Query("SELECT id FROM exploration_nodes WHERE id=$1 AND exploration_id=$2 AND kind='intent' FOR SHARE",(intent,scope.exploration_id)))
                    if found!=[(intent,)]:raise ActivityMissing('Local intent unavailable; inherited activity unsupported')
                if selection.action=='detail':
                    rows=tx.fetch_all(Query("SELECT CASE WHEN octet_length(COALESCE(detail,'')) <= $4 THEN COALESCE(detail,'') ELSE NULL END FROM activity WHERE exploration_id=$1 AND id=$2 AND ($3::bigint IS NULL OR node_id=$3)",(scope.exploration_id,selection.seq,intent,self.max_bytes)))
                    if rows and rows[0][0] is None:raise ActivityUnavailable('Activity detail exceeds bound')
                    result={'detail':rows[0][0] if rows else ''}
                else:
                    history=selection.action=='history'
                    condition="a.id<$3 OR $3=0" if history else 'a.id>$3'
                    order='DESC' if history else 'ASC'
                    # Source current segment comes from main_sessions, including empty new sessions.
                    filt="AND a.worker='planner'" if mode=='plan' else ("AND a.worker='mainagent' AND COALESCE(a.main_seg,0)=COALESCE($5::integer,(SELECT COALESCE(MAX(seq),0) FROM main_sessions WHERE exploration_id=$1))" if mode=='main' else '')
                    sql=f"""WITH snap AS (SELECT COALESCE(MAX(id),0) AS cursor FROM activity WHERE exploration_id=$1),
page AS (SELECT a.id,{_PAYLOAD}::text AS payload FROM activity a WHERE a.exploration_id=$1 AND ($2::bigint IS NULL OR a.node_id=$2) AND ({condition}) {filt} ORDER BY a.id {order} LIMIT $4),
sized AS (SELECT id,payload,SUM(octet_length(payload)) OVER() AS bytes FROM page)
SELECT snap.cursor,sized.id,CASE WHEN sized.bytes <= $6 THEN sized.payload ELSE NULL END FROM snap LEFT JOIN sized ON TRUE ORDER BY sized.id {order}"""
                    # $5 must be typed even when session filter is absent.
                    if mode!='main':sql=sql.replace('WITH snap AS','WITH unused AS (SELECT $5::integer), snap AS')
                    rows=tx.fetch_all(Query(sql,(scope.exploration_id,intent,selection.before if history else selection.since,selection.limit+1 if history else selection.limit,arg if mode=='main' else None,self.max_bytes)))
                    if not rows or len(rows)>selection.limit+1:raise ActivityUnavailable('Invalid activity page')
                    snapshot=rows[0][0];items=[]
                    for cursor,seq,payload in rows:
                        if type(cursor) is not int or cursor<0 or cursor!=snapshot:raise ActivityUnavailable('Invalid snapshot cursor')
                        if seq is None:continue
                        if payload is None:raise ActivityUnavailable('Activity page exceeds bound')
                        item=json.loads(payload)
                        if type(item) is not dict or item.get('seq')!=seq:raise ActivityUnavailable('Invalid activity row')
                        items.append(item)
                    if history:
                        more=len(items)>selection.limit;items=list(reversed(items[:selection.limit]))
                        result={'items':items,'snapshot_cursor':snapshot,'earliest_cursor':items[0]['seq'] if items else selection.before,'has_more':more}
                    else:result={'items':items,'cursor':items[-1]['seq'] if items else selection.since}
                self.validate_read(selection,pins)
            self.validate_read(selection,pins)
        self.validate_read(selection,pins)
        return result
