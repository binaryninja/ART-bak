"""Bounded source-scoped PostgreSQL archive export, no lifecycle mutation.

All 22 Go SnapshotTaskArchive queries, bounded per-row and total. Read-only
repeatable-read transaction begins before schema lock and reads; injected
quiescence_guard(task_id) must hold task/main/planner/worker drainage and deletion
barriers through commit. It is not provided by this module. No live DB defaults.

Tables alone are NOT a restore-ready manifest: exclusive-host/asset ownership
and aggregate-stat computations remain explicit metadata(tx,task_id,exp_id,tables)
capability using SAME snapshot. Missing callback rejects before any database read.
Evidence blobs, transcripts/workspaces are separately supplied under the same
host-owned lifetime barrier. This module never reads files, packages incomplete
payloads, transitions archives, prunes/restores tables or guesses ownership.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import json
from .db.core import Query,int64
from .db.postgres import NoRowsError
from .archive_bundle import _json,BundleInvalid

class SnapshotUnavailable(RuntimeError):pass
class SnapshotLimit(RuntimeError):pass

QUERIES = (('tasks', 'SELECT * FROM tasks WHERE id=$1', ('taskID',)), ('explorations', 'SELECT * FROM explorations WHERE id=$1', ('expID',)), ('exploration_nodes', 'SELECT * FROM exploration_nodes WHERE exploration_id=$1 ORDER BY id', ('expID',)), ('exploration_edges', 'SELECT * FROM exploration_edges WHERE exploration_id=$1 ORDER BY src_id,dst_id', ('expID',)), ('exploration_anchors', 'SELECT anchor.* FROM exploration_anchors anchor JOIN exploration_nodes node ON node.id=anchor.node_id WHERE node.exploration_id=$1 ORDER BY node_id,asset_id', ('expID',)), ('task_constraints', 'SELECT * FROM task_constraints WHERE exploration_id=$1 ORDER BY id', ('expID',)), ('activity', 'SELECT * FROM activity WHERE exploration_id=$1 ORDER BY id', ('expID',)), ('task_relations', 'SELECT * FROM task_relations WHERE task_id=$1 ORDER BY created_at,source_task_id', ('taskID',)), ('task_asset_links', 'SELECT * FROM task_asset_links WHERE task_id=$1 ORDER BY asset_id', ('taskID',)), ('task_llm_profiles', 'SELECT * FROM task_llm_profiles WHERE task_id=$1 ORDER BY position', ('taskID',)), ('task_scope', 'SELECT * FROM task_scope WHERE task_id=$1 ORDER BY id', ('taskID',)), ('findings', 'SELECT * FROM findings WHERE task_id=$1 ORDER BY id', ('taskID',)), ('finding_traffic_bindings', 'SELECT b.* FROM finding_traffic_bindings b JOIN findings f ON f.id=b.finding_id WHERE f.task_id=$1 ORDER BY b.finding_id,b.position,b.id', ('taskID',)), ('traffic_evidence_snapshots', 'SELECT s.* FROM traffic_evidence_snapshots s WHERE EXISTS(SELECT 1 FROM finding_traffic_bindings b JOIN findings f ON f.id=b.finding_id WHERE b.snapshot_id=s.id AND f.task_id=$1) ORDER BY s.id', ('taskID',)), ('llm_records', "SELECT * FROM llm_records WHERE COALESCE(task_id,'')=$1 ORDER BY id", ('task_string',)), ('llm_usage', "SELECT * FROM llm_usage WHERE COALESCE(task_id,'')=$1 OR exploration_id=$2 ORDER BY id", ('task_string', 'expID')), ('skill_usage', 'SELECT * FROM skill_usage WHERE task_id=$1 OR exploration_id=$2 ORDER BY id', ('taskID', 'expID')), ('tool_usage', 'SELECT * FROM tool_usage WHERE task_id=$1 OR exploration_id=$2 ORDER BY id', ('taskID', 'expID')), ('intercept_pending', "SELECT * FROM intercept_pending WHERE COALESCE(task_id,'')=$1 ORDER BY id", ('task_string',)), ('side_question_sessions', 'SELECT * FROM side_question_sessions WHERE task_id=$1 ORDER BY session_key', ('taskID',)), ('side_question_requests', 'SELECT r.* FROM side_question_requests r JOIN side_question_sessions s ON s.session_key=r.session_key WHERE s.task_id=$1 ORDER BY r.ordinal', ('taskID',)), ('assets', "SELECT asset.* FROM assets asset WHERE asset.id IN (SELECT id FROM assets WHERE $1=ANY(task_ids)\nUNION SELECT link.asset_id FROM task_asset_links link WHERE link.task_id=$1\nUNION SELECT anchor.asset_id FROM exploration_anchors anchor\n      JOIN exploration_nodes node ON node.id=anchor.node_id WHERE node.exploration_id=$2\nUNION SELECT value::bigint FROM findings finding\n      CROSS JOIN LATERAL jsonb_array_elements_text(\n        CASE WHEN jsonb_typeof(finding.asset_ids)='array' THEN finding.asset_ids ELSE '[]'::jsonb END\n      ) value WHERE finding.task_id=$1 AND value ~ '^[0-9]+$') ORDER BY asset.id", ('taskID', 'expID')))

@dataclass(frozen=True)
class SnapshotExport:
    manifest:dict
    files:dict

class ArchiveSnapshotExporter:
    def __init__(self,executor,*,quiescence_guard=None,metadata=None,max_rows=10000,max_bytes=64<<20,clock=None):
        if type(max_rows) is not int or not 1<=max_rows<=1000000 or type(max_bytes) is not int or not 1<=max_bytes<=1024**3:raise ValueError('Invalid export limits')
        self.executor,self.guard,self.metadata=executor,quiescence_guard,metadata
        self.max_rows,self.max_bytes=max_rows,max_bytes;self.clock=clock or (lambda:datetime.now(timezone.utc))
    def export(self,task_id):
        int64(task_id,'task ID')
        if task_id<=0:raise ValueError('Positive task ID required')
        if not callable(self.guard) or not callable(self.metadata) or not callable(getattr(self.executor,'archive_snapshot_transaction',None)):raise SnapshotUnavailable('Quiescence, same-snapshot metadata and transaction adapters required')
        with self.guard(task_id):
            with self.executor.archive_snapshot_transaction() as tx:
                try:
                    tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(7337741001,)))
                    row=tx.fetch_one(Query('SELECT exploration_id FROM tasks WHERE id=$1 AND deleted_at IS NULL',(task_id,)))
                except NoRowsError:raise SnapshotUnavailable('Live task unavailable') from None
                if row is None or len(row)!=1:raise SnapshotUnavailable('Live task unavailable')
                exp_id=int64(row[0],'exploration ID')
                if exp_id<=0:raise SnapshotUnavailable('Task exploration unavailable')
                values={'taskID':task_id,'expID':exp_id,'task_string':str(task_id)}
                tables={};counts={};total=0;stream=b''
                for name,inner,selectors in QUERIES:
                    params=tuple(values[key] for key in selectors);bound=len(params)+1
                    # Bound row count in SQL and prevent oversized JSON from crossing
                    # the executor boundary; PostgreSQL still needs to serialize rows.
                    sql=('SELECT CASE WHEN octet_length(raw)<=$'+str(bound)+' THEN raw ELSE NULL END, octet_length(raw) FROM (SELECT row_to_json(row_data)::text AS raw FROM ('+inner+') row_data LIMIT $'+str(bound+1)+') bounded')
                    rows=tx.fetch_all(Query(sql,params+(self.max_bytes,self.max_rows+1)))
                    if len(rows)>self.max_rows:raise SnapshotLimit('Archive table row limit exceeded')
                    parsed=[];encoded=[]
                    for raw,length in rows:
                        if raw is None or type(length) is not int or length<0 or length>self.max_bytes:raise SnapshotLimit('Archive row byte limit exceeded')
                        payload=raw.encode('utf-8') if isinstance(raw,str) else raw
                        if not isinstance(payload,bytes) or len(payload)!=length:raise SnapshotUnavailable('Invalid archive row encoding')
                        total+=length+1
                        if total>self.max_bytes:raise SnapshotLimit('Archive total byte limit exceeded')
                        value=_json(payload)
                        if not isinstance(value,dict):raise SnapshotUnavailable('Archive row must be an object')
                        parsed.append(value);encoded.append(payload+b'\n')
                    counts[name]=len(parsed)
                    if name=='llm_records':stream=b''.join(encoded);tables[name]=[]
                    else:tables[name]=parsed
                if len(tables['tasks'])!=1 or tables['tasks'][0].get('id')!=task_id or len(tables['explorations'])!=1 or tables['explorations'][0].get('id')!=exp_id:raise SnapshotUnavailable('Archive parent scope mismatch')
                extra=self.metadata(tx,task_id,exp_id,tables)
                if not isinstance(extra,dict) or set(extra)!={'hosts','exclusive_hosts','exclusive_asset_ids','aggregate_stats'}:raise SnapshotUnavailable('Incomplete archive ownership/aggregate metadata')
                for key in ('hosts','exclusive_hosts'):
                    if not isinstance(extra[key],list) or any(not isinstance(v,str) for v in extra[key]):raise SnapshotUnavailable('Invalid host metadata')
                if not isinstance(extra['exclusive_asset_ids'],list) or any(type(v) is not int or v<=0 for v in extra['exclusive_asset_ids']) or not isinstance(extra['aggregate_stats'],dict):raise SnapshotUnavailable('Invalid asset/aggregate metadata')
                source_ids=[]
                for relation in tables['task_relations']:
                    if relation.get('task_id')!=task_id or type(relation.get('source_task_id')) is not int or relation['source_task_id']<=0:raise SnapshotUnavailable('Invalid task relation')
                    source_ids.append(relation['source_task_id'])
                now=self.clock()
                if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:raise ValueError('Aware archive clock required')
                manifest=dict(format_version=3,created_at=now.astimezone(timezone.utc).isoformat().replace('+00:00','Z'),task_id=task_id,exploration_id=exp_id,source_task_ids=source_ids,tables=tables,data_counts=counts,streamed_tables={'llm_records':'database/llm_records.ndjson'},**extra)
                # Defensive serializable copy; no raw credentials/errors logged.
                try:encoded=json.dumps(manifest,allow_nan=False).encode('utf-8')
                except (ValueError,TypeError):raise SnapshotUnavailable('Invalid archive metadata') from None
                if len(encoded)+len(stream)>self.max_bytes:raise SnapshotLimit('Archive manifest byte limit exceeded')
                return SnapshotExport(_json(encoded),{'database/llm_records.ndjson':stream})
