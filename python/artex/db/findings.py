"""Standalone finding persistence, filters and version-checked report writes.

Global administrator reads are default; optional task/exploration scope restricts
ALL operations and never expands inheritance. This ports low-level AddFinding,
not RecordFinding's graph creation, notifications or traffic preparation. No
finding-level deduplication is invented: source AddFinding inserts every row.
set_status is explicitly LOW LEVEL and DOES NOT enqueue notifications: production
HTTP status changes must remain unavailable until the notification transaction
adapter is wired. Metadata mirrors are atomic instead of upstream best-effort.
Explicit invalid task filters are rejected rather than silently becoming global
(an intentional tightening of upstream behavior). Asset subtrees, export traffic
staging, evidence edits and deletion are unported.
Report writes preserve evidence lock order, archive guards, and stale-version
semantics. Mock tests verify SQL contracts, not live PostgreSQL behavior.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
from .core import Query, int64

STATUSES = ('pending','in_progress','confirmed','resolved','fixed','false_positive','ignored','duplicate','risk_accepted')
SEVERITIES = ('critical','high','medium','low')


class FindingInvalid(ValueError): pass
class FindingNotFound(LookupError): pass
class EvidenceConflict(RuntimeError): pass
class FindingArchiveState(RuntimeError): pass


@dataclass
class Finding:
    id: int
    task_id: int | None
    node_id: int | None
    vulnclass: str
    name: str
    severity: str
    summary: str
    evidence: str
    worker: str
    asset_ids: list[int]
    status: str
    created_at: datetime
    task_description: str
    evidence_version: int
    report_evidence_version: int
    traffic_count: int
    report: str = ''
    traffic_bindings: list = field(default_factory=list)

    def __post_init__(self):
        if isinstance(self.asset_ids,(str,bytes)):
            self.asset_ids=json.loads(self.asset_ids)
        if self.asset_ids is None:
            self.asset_ids=[]
        if not isinstance(self.asset_ids,list) or any(type(i) is not int for i in self.asset_ids):
            raise FindingInvalid('stored finding asset_ids is not an integer array')

    def to_dict(self):
        result=asdict(self)
        result['created_at']=self.created_at.isoformat()
        return result


@dataclass(frozen=True)
class FindingFilter:
    severity: str = ''
    status: str = ''
    vulnclass: str = ''
    task_id: str = ''
    query: str = ''
    sort: str = ''
    asset_scope: str = ''


COLS="""f.id, f.task_id, f.node_id, f.vulnclass, COALESCE(f.name,''), f.severity, f.summary,
f.evidence, f.worker, f.asset_ids, COALESCE(f.status,'pending'), f.created_at,
COALESCE(t.description,''), f.evidence_version, f.report_evidence_version,
(SELECT count(*) FROM finding_traffic_bindings b WHERE b.finding_id=f.id)"""
FROM=' FROM findings f LEFT JOIN tasks t ON f.task_id=t.id'


def _text(value):
    if not isinstance(value,str):raise FindingInvalid('finding text must be a string')
    return value


def _positive(value,name='finding id'):
    value=int64(value,name)
    if value<=0:raise FindingInvalid(f'{name} must be positive')
    return value


class FindingStore:
    def __init__(self,executor,*,task_id=None,exploration_id=None):
        self.executor=executor
        self.task_id=None if task_id is None else _positive(task_id,'task id')
        self.exploration_id=None if exploration_id is None else _positive(exploration_id,'exploration id')

    def _scope(self,args,alias='f'):
        clauses=[]
        if self.task_id is not None:
            args.append(self.task_id);clauses.append(f'{alias}.task_id=${len(args)}')
        if self.exploration_id is not None:
            args.append(self.exploration_id)
            clauses.append(f'EXISTS(SELECT 1 FROM exploration_nodes scope_node WHERE scope_node.id={alias}.node_id AND scope_node.exploration_id=${len(args)})')
        return clauses

    def _where(self,filter):
        args=[];clauses=self._scope(args)
        for column,value in [('severity',filter.severity),('status',filter.status),('vulnclass',filter.vulnclass)]:
            _text(value)
            if value:
                args.append(value);clauses.append(f'f.{column}=${len(args)}')
        if filter.task_id=='__unassigned__':clauses.append('(f.task_id IS NULL OR t.id IS NULL)')
        elif filter.task_id != '':
            selector = filter.task_id
            if type(selector) is int:
                tid = selector
            elif isinstance(selector, str) and selector.isascii() and selector.isdecimal():
                try:
                    tid = int(selector)
                except ValueError as error:
                    raise FindingInvalid('task filter must be a positive signed 64-bit ID') from error
            else:
                raise FindingInvalid('task filter must be a positive signed 64-bit ID')
            try:
                tid = _positive(tid, 'task filter')
            except ValueError as error:
                raise FindingInvalid('task filter must be a positive signed 64-bit ID') from error
            args.append(tid);clauses.append(f'f.task_id=${len(args)}')
        if filter.asset_scope=='__none__':
            clauses.append("(jsonb_array_length(COALESCE(f.asset_ids,'[]'::jsonb))=0 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(f.asset_ids) e(v) JOIN assets a ON a.id=e.v::bigint))")
        elif filter.asset_scope:raise NotImplementedError('finding asset subtree resolution is not ported')
        query=_text(filter.query).strip()
        if query:
            args.append('%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
            bind=f'${len(args)}'
            clauses.append('('+' OR '.join(f"COALESCE(f.{col},'') ILIKE {bind} ESCAPE '\\'" for col in ('name','vulnclass','summary','evidence','report'))+')')
        return (' WHERE '+' AND '.join(clauses) if clauses else ''),args

    def list(self,limit=500):
        limit=int64(limit,'limit');limit=limit if limit>0 else 500
        where,args=self._where(FindingFilter());args.append(limit)
        return [Finding(*row) for row in self.executor.fetch_all(Query('SELECT '+COLS+FROM+where+f' ORDER BY f.created_at DESC, f.id DESC LIMIT ${len(args)}',tuple(args)))]

    def list_page(self,filter=None,page=1,page_size=20):
        filter=filter or FindingFilter()
        page=max(1,int64(page,'page'));page_size=int64(page_size,'page size');page_size=page_size if page_size>0 else 20
        where,args=self._where(filter)
        total=self.executor.fetch_one(Query('SELECT count(*)'+FROM+where,tuple(args)))[0]
        if total==0 or page>(total-1)//page_size+1:return [],total
        order="CASE f.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC, " if filter.sort=='severity' else ''
        params=tuple(args)+(page_size,(page-1)*page_size)
        rows=self.executor.fetch_all(Query('SELECT '+COLS+FROM+where+' ORDER BY '+order+f'f.created_at DESC, f.id DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}',params))
        return [Finding(*row) for row in rows],total

    def get(self,id):
        args=[_positive(id)];clauses=['f.id=$1']+self._scope(args)
        rows=self.executor.fetch_all(Query('SELECT '+COLS+", COALESCE(f.report,'')"+FROM+' WHERE '+' AND '.join(clauses),tuple(args)))
        return Finding(*rows[0]) if rows else None

    def _transaction(self):
        transaction=getattr(self.executor,'transaction',None)
        if transaction is None:raise TypeError('finding mutations require a transaction-capable executor')
        return transaction()

    @staticmethod
    def _lock_task(tx,task_id):
        if not task_id:return
        rows=tx.fetch_all(Query('SELECT deleted_at FROM tasks WHERE id=$1 FOR UPDATE',(task_id,)))
        if not rows:raise FindingNotFound('finding task does not exist')
        if rows[0][0] is not None:raise FindingArchiveState('task is deleted or archived')
        archive=tx.fetch_all(Query('SELECT state FROM task_archives WHERE task_id=$1',(task_id,)))
        if archive and archive[0][0]!='failed':raise FindingArchiveState('task has an active archive operation')

    def create(self,*,task_id=0,node_id=0,vulnclass='',name='',severity,summary='',evidence='',worker='',asset_ids=()):
        if severity not in SEVERITIES:raise FindingInvalid('invalid severity')
        for value in (vulnclass,name,summary,evidence,worker):_text(value)
        tid=int64(task_id,'task id');nid=int64(node_id,'node id')
        if tid<0 or nid<0:raise FindingInvalid('task and node ids cannot be negative')
        if self.task_id is not None and tid!=self.task_id:raise FindingInvalid('task outside store scope')
        if self.exploration_id is not None and not nid:raise FindingInvalid('scoped finding requires a node')
        assets=[_positive(value,'asset id') for value in asset_ids] # Preserve upstream array/order, not invented dedup.
        with self._transaction() as tx:
            tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(7337741004,)))
            self._lock_task(tx,tid)
            if nid:
                args=[nid];conditions=["n.id=$1","n.kind='finding'"]
                if tid:
                    args.append(tid);conditions.append(f'EXISTS(SELECT 1 FROM tasks t WHERE t.id=${len(args)} AND t.exploration_id=n.exploration_id AND t.deleted_at IS NULL)')
                if self.exploration_id is not None:
                    args.append(self.exploration_id);conditions.append(f'n.exploration_id=${len(args)}')
                if not tx.fetch_all(Query('SELECT n.id FROM exploration_nodes n WHERE '+' AND '.join(conditions)+' FOR KEY SHARE OF n',tuple(args))):
                    raise FindingInvalid('finding node does not belong to the task/exploration')
            id=tx.fetch_one(Query('''INSERT INTO findings(task_id,node_id,vulnclass,name,severity,summary,evidence,worker,asset_ids)
VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id''',(tid or None,nid or None,vulnclass,name,severity,summary,evidence,worker,json.dumps(assets))))[0]
        return id

    def set_status(self,id,status):
        """Low-level status ONLY. Production notification path is not ported."""
        if status not in STATUSES:raise FindingInvalid('invalid finding status')
        args=[status,_positive(id)];clauses=['f.id=$2']+self._scope(args)
        return self.executor.execute(Query('UPDATE findings f SET status=$1 WHERE '+' AND '.join(clauses),tuple(args)))

    def _set_text(self,id,column,value):
        if column not in ('name','vulnclass','severity','summary'):raise FindingInvalid('unsupported field')
        _text(value);args=[value,_positive(id)];clauses=['f.id=$2']+self._scope(args)
        with self._transaction() as tx:
            rows=tx.fetch_all(Query(f'UPDATE findings f SET {column}=$1 WHERE '+' AND '.join(clauses)+' RETURNING f.node_id, f.task_id',tuple(args)))
            if not rows:return 0
            node_id,task_id=rows[0]
            if node_id is not None:
                # Match the originating task exploration; never write inherited nodes.
                tx.execute(Query(f'''UPDATE exploration_nodes n
SET payload=jsonb_set(payload, '{{{column}}}', to_jsonb($1::text)), content_version=content_version+1
WHERE n.id=$2 AND n.kind='finding' AND ($3::bigint IS NULL OR EXISTS(
 SELECT 1 FROM tasks t WHERE t.id=$3 AND t.exploration_id=n.exploration_id))''',(value,node_id,task_id)))
        return 1

    def set_name(self,id,value):return self._set_text(id,'name',value)
    def set_vulnclass(self,id,value):return self._set_text(id,'vulnclass',value)
    def set_summary(self,id,value):return self._set_text(id,'summary',value)
    def set_severity(self,id,value):
        if value not in SEVERITIES:raise FindingInvalid('invalid severity')
        return self._set_text(id,'severity',value)

    def set_report_by_node_id(self,node_id,report,version=None):
        _text(report);node_id=_positive(node_id,'node id')
        if version is not None and int64(version,'evidence version')<0:raise FindingInvalid('negative evidence version')
        args=[node_id];clauses=['f.node_id=$1']+self._scope(args)
        with self._transaction() as tx:
            tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(7337741004,)))
            rows=tx.fetch_all(Query('SELECT f.id, f.task_id FROM findings f WHERE '+' AND '.join(clauses),tuple(args)))
            if not rows:return 0
            if len(rows)>1:raise FindingInvalid('multiple findings reference this node; select an unambiguous record')
            id,task_id=rows[0];self._lock_task(tx,task_id)
            current=tx.fetch_one(Query('SELECT evidence_version FROM findings WHERE id=$1 FOR UPDATE',(id,)))[0]
            if version is not None and version!=current:raise EvidenceConflict('traffic evidence changed; refresh before retrying')
            affected=tx.execute(Query('''UPDATE findings SET report=$2,
report_evidence_version=COALESCE($3::bigint,CASE WHEN evidence_version=0 THEN 0 ELSE -1 END) WHERE id=$1''',(id,report,version)))
        return affected

    def delete(self,id):raise NotImplementedError('finding deletion requires evidence-store cleanup')


FindingsStore=FindingStore
