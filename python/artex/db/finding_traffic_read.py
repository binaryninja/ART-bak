"""Bounded fixed-task evidence metadata list; no blob/header reads or mutations.

Source db.FindingTrafficTx ordering/versions and evidence advisory lock. Hashes
are references with strict syntax, NOT proof of metadata/blob integrity. Global,
inherited and archived findings are deliberately unsupported by this scope.
"""
from dataclasses import dataclass,fields
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import json,re
from .core import Query

class FindingTrafficDenied(PermissionError):pass
class FindingTrafficMissing(LookupError):pass
class FindingTrafficUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class FindingTrafficScope:
    task_id:int
    exploration_id:int
    finding_id:int
    revision:str
    def validate(self):
        if type(self) is not FindingTrafficScope or any(type(v) is not int or not 0<v<2**63 for v in (self.task_id,self.exploration_id,self.finding_id)) or type(self.revision) is not str or not 1<=len(self.revision.encode())<=256 or '\0' in self.revision:
            raise ValueError('Exact bounded local finding scope required')

def _identity(scope):return tuple((type(getattr(scope,f.name)),getattr(scope,f.name)) for f in fields(scope))
def _integer(v,minimum=0):
    if type(v) is not int or not minimum<=v<2**63:raise FindingTrafficUnavailable('Invalid metadata integer')
    return v
def _text(v):
    if type(v) is not str or '\0' in v:raise FindingTrafficUnavailable('Invalid metadata text')
    v.encode('utf-8');return v
def _hash(v):
    if type(v) is not str or re.fullmatch('[0-9a-f]{64}',v) is None:raise FindingTrafficUnavailable('Invalid evidence reference')
    return v
def _date(v):
    if type(v) is not datetime or type(v.tzinfo) not in (timezone,ZoneInfo):raise FindingTrafficUnavailable('Invalid metadata timestamp')
    return v.astimezone(timezone.utc).isoformat().replace('+00:00','Z')

class FindingTrafficReader:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard,max_rows=1000,max_bytes=2097152):
        if type(scope) is not FindingTrafficScope:raise ValueError('Exact finding scope required')
        scope.validate()
        if any(not callable(c) for c in (authorize,current,lifecycle_guard,getattr(executor,'transaction',None))):raise ValueError('Explicit storage and metadata authority required')
        if type(max_rows) is not int or not 1<=max_rows<=1000 or type(max_bytes) is not int or not 1024<=max_bytes<=2097152:raise ValueError('Bounded metadata limits required')
        self.executor,self.scope=executor,scope;self.authorize,self.current,self.guard=authorize,current,lifecycle_guard
        self.max_rows,self.max_bytes=max_rows,max_bytes
        self._pins=(executor,scope,authorize,current,lifecycle_guard,max_rows,max_bytes,executor.transaction)
        self._original=self._pins;self._scope_identity=_identity(scope)
    def check(self):
        e,s,a,c,g,n,b,tx=self._original
        if self._pins is not self._original or any(x is not y for x,y in zip((self.executor,self.scope,self.authorize,self.current,self.guard),(e,s,a,c,g))) or type(self.max_rows) is not int or self.max_rows!=n or type(self.max_bytes) is not int or self.max_bytes!=b or e.transaction!=tx or _identity(s)!=self._scope_identity:raise FindingTrafficDenied('Metadata binding changed')
        s.validate()
    def read(self,request_authorize):
        if not callable(request_authorize):raise ValueError('Explicit request metadata grant required')
        e,s,a,c,g,n,b,txmethod=self._original
        def allow():
            self.check()
            if c(s) is not True:raise FindingTrafficDenied()
            self.check()
            if a(s) is not True:raise FindingTrafficDenied()
            self.check()
            if c(s) is not True:raise FindingTrafficDenied()
            self.check()
            if a(s) is not True:raise FindingTrafficDenied()
            self.check()
            if request_authorize(s) is not True:raise FindingTrafficDenied()
            self.check()
        allow()
        with g(s):
            allow()
            with txmethod() as tx:
                tx.fetch_all(Query('SELECT pg_advisory_xact_lock($1)',(7337741004,)));allow()
                rows=tx.fetch_all(Query("""SELECT f.id,f.evidence_version,f.report_evidence_version
FROM findings f JOIN tasks t ON t.id=f.task_id
JOIN exploration_nodes node ON node.id=f.node_id AND node.exploration_id=t.exploration_id AND node.kind='finding'
WHERE f.id=$1 AND t.id=$2 AND t.exploration_id=$3 AND t.deleted_at IS NULL AND t.archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives a WHERE a.task_id=t.id) FOR SHARE OF f,t,node""",(s.finding_id,s.task_id,s.exploration_id)))
                allow()
                if not rows:raise FindingTrafficMissing('Scoped finding unavailable')
                if type(rows) not in (tuple,list) or len(rows)!=1 or len(rows[0])!=3 or type(rows[0][0]) is not int or rows[0][0]!=s.finding_id:raise FindingTrafficUnavailable('Finding identity unavailable')
                version=_integer(rows[0][1])
                # Source regeneration marks a report stale with the exact -1 sentinel.
                report_version=_integer(rows[0][2],-1)
                # Deliberately do not select snapshot headers, body data or to_jsonb(s).
                names=('snapshot_id','role','note','sid','source_traffic_id','url','method','content_type','req_hash','resp_hash')
                size='+'.join("octet_length(COALESCE("+k+",''))" for k in names)
                projection=','.join('CASE WHEN bytes<=$2 THEN '+k+' END' if k in names else k for k in ('id','finding_id','snapshot_id','role','note','position','binding_created','sid','source_traffic_id','captured_at','url','method','status','content_type','req_hash','resp_hash','req_len','resp_len','snapshot_created'))
                rows=tx.fetch_all(Query("""WITH selected AS (
SELECT b.id,b.finding_id,b.snapshot_id,b.role,b.note,b.position,b.created_at AS binding_created,
s.id AS sid,s.source_traffic_id,s.captured_at,s.url,s.method,s.status,s.content_type,s.req_hash,s.resp_hash,s.req_len,s.resp_len,s.created_at AS snapshot_created
FROM finding_traffic_bindings b LEFT JOIN traffic_evidence_snapshots s ON s.id=b.snapshot_id
WHERE b.finding_id=$1), bounded AS (SELECT *,SUM("""+size+") OVER() AS bytes FROM selected) SELECT "+projection+",bytes FROM bounded ORDER BY position,id LIMIT $3",(s.finding_id,b,n+1)))
                allow()
                if type(rows) not in (tuple,list) or len(rows)>n:raise FindingTrafficUnavailable('Metadata row bound exceeded')
                bindings=[];seen=set();last=None
                for row in rows:
                    if type(row) not in (tuple,list) or len(row)!=20:raise FindingTrafficUnavailable('Invalid metadata row')
                    bid,fid,sid,role,note,pos,created,snapshot_id,source,captured,url,method,status,ctype,rhash,shash,rlen,slen,screated,byte_count=row
                    _integer(bid,1);_integer(fid,1);_integer(pos);_integer(byte_count)
                    if fid!=s.finding_id or bid in seen or byte_count>b or sid!=snapshot_id:raise FindingTrafficUnavailable('Invalid binding references or bounds')
                    order=(pos,bid)
                    if last is not None and order<=last:raise FindingTrafficUnavailable('Invalid metadata order')
                    seen.add(bid);last=order
                    _hash(sid);_hash(rhash);_hash(shash)
                    if _text(role) not in ('baseline','proof','verification','supporting'):raise FindingTrafficUnavailable('Invalid binding role')
                    snapshot=dict(id=sid,source_traffic_id=_text(source),captured_at=_integer(captured),url=_text(url),method=_text(method),status=_integer(status),content_type=_text(ctype),req_hash=rhash,resp_hash=shash,req_len=_integer(rlen),resp_len=_integer(slen),created_at=_date(screated))
                    bindings.append(dict(id=str(bid),finding_id=str(fid),snapshot_id=sid,role=role,note=_text(note),position=pos,created_at=_date(created),snapshot=snapshot))
                result=dict(finding_id=str(s.finding_id),version=version,report_version=report_version,bindings=bindings)
                if len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode())>b:raise FindingTrafficUnavailable('Metadata response bound exceeded')
                allow()
            allow()
        allow()
        def disclosure():
            with g(s):allow()
            allow()
        return result,disclosure
