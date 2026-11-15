"""Task-scoped durable archive job state, not archive execution.

Source: db/task_archives.go/schema.sql. No package/filesystem work, snapshot data
export, task compaction, restore or deletion implementation; no ready/completed
setter is exposed. Queueing persists intent only, never claims work was executed.
This facade is scoped to one explicit task; a global FIFO orchestration worker is
not supplied. claim() only takes that task's archive_queued job, not restore/delete
jobs whose execution prerequisites have not been ported.

Queue/claim require lifecycle_guard(task_id,operation) context manager held through
transaction commit. It must share task admission/deletion barriers and verify all
workers/side questions/recorders are drained as appropriate; a no-op guard is not
production safe. SQL task row locks alone cannot stop in-memory execution.
Progress/failure uses state + updated_at CAS and returns a fresh job record; caller
must use that new revision for the next write. This strengthens unguarded upstream
progress/failure writes, but is not a worker lease across a restore/retry cycle.
Schema update triggers are required. No automatic recovery or retry is performed.
"""
from contextlib import contextmanager
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
import json
from .core import Query,int64

class ArchiveInvalid(ValueError):pass
class ArchiveMissing(LookupError):pass
class ArchiveConflict(RuntimeError):pass
class ArchiveUnavailable(RuntimeError):pass

STATES=frozenset(('archive_queued','archiving','archive_failed','ready','restore_queued','restoring','restore_failed','delete_queued','deleting','delete_failed'))
FAILED={'archiving':'archive_failed','restoring':'restore_failed','deleting':'delete_failed'}
FORMAT_VERSION=3
COLS="id, task_id, state, phase, progress, COALESCE(error,''), warnings,\nformat_version, COALESCE(archive_path,''), COALESCE(sha256,''), original_size,\ncompressed_size, COALESCE(task_name,''), COALESCE(task_description,''),\nCOALESCE(task_goal,''), COALESCE(original_status,''), category_id_snapshot,\nCOALESCE(category_name_snapshot,''), array_to_json(source_task_ids)::text,\nremaining_timeout_seconds, data_counts, aggregate_stats, archived_at,\nrequested_at, created_at, updated_at"
_TASK="SELECT COALESCE(t.name,''), t.description, t.goal, t.status,\n t.category_id, COALESCE(c.name,''), t.paused, t.queued, t.deadline_at\nFROM tasks t LEFT JOIN task_categories c ON c.id=t.category_id\nWHERE t.id=$1 AND t.deleted_at IS NULL FOR UPDATE OF t"
_DEPENDENTS="SELECT child.id FROM task_relations relation\nJOIN tasks child ON child.id=relation.task_id AND child.deleted_at IS NULL\nLEFT JOIN task_archives pending ON pending.task_id=child.id\nWHERE relation.source_task_id=$1\n  AND (pending.id IS NULL OR pending.state NOT IN ('archive_queued','archiving'))\nLIMIT 1"
_SOURCES='SELECT source_task_id FROM task_relations WHERE task_id=$1 ORDER BY created_at, source_task_id'
_QUEUE="INSERT INTO task_archives(\n task_id,state,phase,progress,error,warnings,format_version,task_name,\n task_description,task_goal,original_status,category_id_snapshot,\n category_name_snapshot,source_task_ids,remaining_timeout_seconds,requested_at)\nVALUES ($1,$2,'queued',0,'','[]',$3,$4,$5,$6,$7,$8,$9,$10,$11,now())\n\tON CONFLICT (task_id) DO UPDATE SET\n\t state=CASE WHEN task_archives.state IN ('archive_failed') THEN EXCLUDED.state ELSE task_archives.state END,\n\t phase=CASE WHEN task_archives.state IN ('archive_failed') THEN 'queued' ELSE task_archives.phase END,\n\t progress=CASE WHEN task_archives.state IN ('archive_failed') THEN 0 ELSE task_archives.progress END,\n\t error=CASE WHEN task_archives.state IN ('archive_failed') THEN '' ELSE task_archives.error END,\n\t format_version=CASE WHEN task_archives.state IN ('archive_failed') THEN EXCLUDED.format_version ELSE task_archives.format_version END,\n\t requested_at=CASE WHEN task_archives.state IN ('archive_failed') THEN now() ELSE task_archives.requested_at END"

def _id(value):
    if int64(value,'ID')<=0:raise ArchiveInvalid('ID must be positive')
    return value


def _text(value):
    if not isinstance(value,str) or '\x00' in value:raise ArchiveInvalid('Invalid archive text')
    value.encode('utf-8');return value


@dataclass
class Archive:
    id:int;task_id:int;state:str;phase:str;progress:int;error:str;warnings:list
    format_version:int;archive_path:str;sha256:str;original_size:int;compressed_size:int
    task_name:str;task_description:str;task_goal:str;original_status:str
    category_id_snapshot:int|None;category_name_snapshot:str;source_task_ids:list
    remaining_timeout_seconds:int;data_counts:dict;aggregate_stats:dict
    archived_at:datetime|None;requested_at:datetime;created_at:datetime;updated_at:datetime
    def to_dict(self):
        result=asdict(self);result.pop('archive_path')
        result['category_id']=result.pop('category_id_snapshot');result['category_name']=result.pop('category_name_snapshot')
        for key in ('archived_at','requested_at','created_at','updated_at'):
            if result[key] is not None:result[key]=result[key].isoformat()
        for key in ('error','sha256','category_id','category_name','archived_at'):
            if result[key] in (None,''):result.pop(key)
        return result


def _decode(row):
    if len(row)!=26:raise ArchiveUnavailable('Invalid archive record')
    values=list(row)
    for index,kind in ((6,list),(18,list),(20,dict),(21,dict)):
        value=values[index]
        if isinstance(value,(str,bytes)):value=json.loads(value)
        value=kind() if value is None else value
        if not isinstance(value,kind):raise ArchiveUnavailable('Invalid archive metadata')
        values[index]=value
    result=Archive(*values)
    if result.state not in STATES:raise ArchiveUnavailable('Invalid archive state')
    return result


class ArchiveStore:
    def __init__(self,executor,task_id,*,lifecycle_guard=None,clock=lambda:datetime.now(timezone.utc)):
        self.executor=executor;self.task_id=_id(task_id);self.lifecycle_guard=lifecycle_guard;self.clock=clock
    @contextmanager
    def _mutation(self,operation):
        if not callable(self.lifecycle_guard) or not callable(getattr(self.executor,'transaction',None)):
            raise ArchiveUnavailable('Lifecycle barrier and transaction adapter required')
        with self.lifecycle_guard(self.task_id,operation):
            with self.executor.transaction() as tx:yield tx
    def _one(self,executor,sql,params):
        rows=executor.fetch_all(Query(sql,params))
        if not rows:return None
        if len(rows)!=1:raise ArchiveUnavailable('Duplicate archive job')
        item=_decode(rows[0])
        if item.task_id!=self.task_id:raise ArchiveUnavailable('Archive task scope mismatch')
        return item
    def get(self,job_id=None):
        sql='SELECT '+COLS+' FROM task_archives WHERE task_id=$1';params=(self.task_id,)
        if job_id is not None:sql+=' AND id=$2';params+=(_id(job_id),)
        return self._one(self.executor,sql,params)
    def queue(self):
        with self._mutation('queue') as tx:
            rows=tx.fetch_all(Query(_TASK,(self.task_id,)))
            if not rows:raise ArchiveMissing('Task not found')
            if len(rows)!=1 or len(rows[0])!=9:raise ArchiveUnavailable('Invalid task lifecycle')
            name,description,goal,status,category,category_name,paused,queued,deadline=rows[0]
            if type(paused) is not bool or type(queued) is not bool:raise ArchiveUnavailable('Invalid task flags')
            if queued:raise ArchiveConflict('Queued task must be paused first')
            if not paused and status not in ('done','failed','timeout'):raise ArchiveConflict('Task must be paused or terminal')
            if tx.fetch_all(Query(_DEPENDENTS,(self.task_id,))):raise ArchiveConflict('Task has a live direct dependent')
            sources=[_id(row[0]) for row in tx.fetch_all(Query(_SOURCES,(self.task_id,)))]
            remaining=0
            if paused and deadline is not None:
                now=self.clock()
                if not isinstance(deadline,datetime) or deadline.tzinfo is None or not isinstance(now,datetime) or now.tzinfo is None:raise ArchiveInvalid('Aware archive clock required')
                remaining=max(0,int((deadline-now).total_seconds()))
            tx.execute(Query(_QUEUE,(self.task_id,'archive_queued',FORMAT_VERSION,name,description,goal,status,category,category_name,sources,remaining)))
            item=self._one(tx,'SELECT '+COLS+' FROM task_archives WHERE task_id=$1',(self.task_id,))
            if item is None or item.state not in ('archive_queued','archive_failed'):raise ArchiveConflict('Archive job cannot be queued from current state')
            return item
    def claim(self):
        with self._mutation('claim') as tx:
            rows=tx.fetch_all(Query("SELECT id,state FROM task_archives WHERE task_id=$1 AND state='archive_queued' ORDER BY requested_at,id FOR UPDATE SKIP LOCKED LIMIT 1",(self.task_id,)))
            if not rows:return None
            key,state=rows[0]
            if state!='archive_queued':raise ArchiveConflict('Unexpected queued state')
            item=self._one(tx,"UPDATE task_archives SET state='archiving',phase='starting',progress=1,error='' WHERE id=$1 AND task_id=$2 AND state=$3 RETURNING "+COLS,(_id(key),self.task_id,state))
            if item is None:raise ArchiveConflict('Archive claim changed')
            return item
    def _revision(self,item):
        if not isinstance(item,Archive) or item.task_id!=self.task_id or item.state not in FAILED:raise ArchiveConflict('Active task-scoped archive revision required')
        if not isinstance(item.updated_at,datetime) or item.updated_at.tzinfo is None:raise ArchiveInvalid('Aware job revision required')
    def progress(self,item,phase,progress):
        self._revision(item);_text(phase)
        if type(progress) is not int:raise ArchiveInvalid('Invalid progress')
        progress=max(0,min(100,progress))
        with self._mutation('progress') as tx:
            result=self._one(tx,'UPDATE task_archives SET phase=$5,progress=$6 WHERE id=$1 AND task_id=$2 AND state=$3 AND updated_at=$4 RETURNING '+COLS,(item.id,self.task_id,item.state,item.updated_at,phase,progress))
            if result is None:raise ArchiveConflict('Archive progress revision changed')
            return result
    def fail(self,item,message='unknown archive failure'):
        self._revision(item);_text(message)
        with self._mutation('fail') as tx:
            result=self._one(tx,"UPDATE task_archives SET state=$5,phase='failed',error=$6 WHERE id=$1 AND task_id=$2 AND state=$3 AND updated_at=$4 RETURNING "+COLS,(item.id,self.task_id,item.state,item.updated_at,FAILED[item.state],message))
            if result is None:raise ArchiveConflict('Archive failure revision changed')
            return result
    def restore(self,*args,**kwargs):raise ArchiveUnavailable('Archive restoration execution is not ported')
    def delete(self,*args,**kwargs):raise ArchiveUnavailable('Archive dependency-safe file/task deletion is not ported')
    def complete(self,*args,**kwargs):raise ArchiveUnavailable('Verified package publication and compaction are not ported')
