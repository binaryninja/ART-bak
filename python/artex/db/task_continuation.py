"""Exact zero-timeout local paused CAS. No schema creation or recovery adoption."""
from .core import Query
from datetime import datetime,timezone

class ContinuationConflict(RuntimeError):pass

class ContinuationPersistence:
    def __init__(self,executor):self.executor=executor;self._executor=executor
    def check(self,tx,receipt,*,paused,stamp=None):
        if self.executor is not self._executor:raise ContinuationConflict('Continuation executor changed')
        rows=tx.fetch_all(Query("""SELECT updated_at FROM tasks WHERE id=$1 AND exploration_id=$2
AND status='running' AND paused=$3 AND NOT queued AND timeout_seconds=0
AND first_run_at IS NULL AND deadline_at IS NULL AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1)
AND NOT EXISTS(SELECT 1 FROM task_relations WHERE task_id=$1) FOR UPDATE""",(receipt.task_id,receipt.exploration_id,paused)))
        if len(rows)!=1 or len(rows[0])!=1 or rows[0][0] is None:raise ContinuationConflict('Exact local continuation task required')
        value=rows[0][0]
        if type(value) is not datetime or type(value.tzinfo) is not timezone:raise ContinuationConflict('Exact aware task timestamp required')
        if stamp is not None and (type(value) is not type(stamp) or value!=stamp):raise ContinuationConflict('Paused task changed')
        fields=(receipt.task_id,receipt.exploration_id,receipt.run_id,receipt.request_id,receipt.config_digest,receipt.owner_token,receipt.revision)
        found=tx.fetch_all(Query("""SELECT task_id FROM public.artex_task_bootstrap
WHERE task_id=$1 AND exploration_id=$2 AND run_id=$3 AND request_id=$4
AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE""",fields))
        if len(found)!=1 or len(found[0])!=1 or type(found[0][0]) is not int or found!=[(receipt.task_id,)]:raise ContinuationConflict('Original preparation changed')
        # A clean stopped generation cannot leave claimed/running local work.
        active=tx.fetch_all(Query("SELECT id FROM exploration_nodes WHERE exploration_id=$1 AND kind='intent' AND state='running' LIMIT 1",(receipt.exploration_id,)))
        if active:raise ContinuationConflict('Running worker claim remains')
        return value
    def resume(self,tx,receipt,stamp):
        # Owner has locked/validated task+bootstrap+claims, then refreshed its
        # grant. Do not insert another callback-bearing read before this CAS.
        rows=tx.fetch_all(Query("""UPDATE tasks SET paused=false,queued=false,queued_at=NULL,queue_mode=''
WHERE id=$1 AND exploration_id=$2 AND status='running' AND paused AND NOT queued
AND timeout_seconds=0 AND first_run_at IS NULL AND deadline_at IS NULL
AND deleted_at IS NULL AND archived_at IS NULL AND updated_at=$3 RETURNING id""",(receipt.task_id,receipt.exploration_id,stamp)))
        if len(rows)!=1 or len(rows[0])!=1 or type(rows[0][0]) is not int or rows!=[(receipt.task_id,)]:raise ContinuationConflict('Continuation CAS lost')
