"""Terminal-only task progress projected from canonical finalized transcripts.

Source capture.go emits per-step text/tool/result records; this bounded first
slice emits ONE durable result per goals/planner/worker turn, with real terminal usage.
It is not live token/tool streaming. A task-row lock serializes receipt lookup and
insert; metadata receipts are private projection bookkeeping, not task completion.
JSONL and PostgreSQL are not one transaction. Any failed projection quarantines
its caller; retry may reconcile the same receipt but must never rerun inference.
No activity is fabricated for an unconfirmed transcript or failed provider call.
"""
from dataclasses import dataclass
from contextlib import nullcontext
from copy import deepcopy
import hashlib,json
from ..chat_transcripts import ChatTranscriptAdapter,_scope_identity
from ..transcripts import TranscriptStore
from ..db.core import Query
from ..db.task_activity import TaskActivityStore,_values
from ..db.conversation_activity import Activity
from .chat import ChatOutcome
from .runtime import Grant,RunResult

class ProjectionUnavailable(RuntimeError):pass
class ProjectionUncertain(RuntimeError):pass

@dataclass(frozen=True)
class ProjectionProposal:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str
    role:str
    node_id:int|None
    turn_id:str
    result_digest:str

class ActivityProjector:
    def __init__(self,executor,scope,*,transcript,current,lifecycle_guard,authorize,max_bytes=1048576):
        if type(transcript) is not ChatTranscriptAdapter or type(transcript.store) is not TranscriptStore:
            raise ValueError('Canonical transcript required')
        if any(not callable(f) for f in (current,lifecycle_guard,authorize)) or type(max_bytes) is not int or not 1<=max_bytes<=1048576:
            raise ValueError('Explicit bounded projection authority required')
        if not callable(getattr(executor,'transaction',None)):raise ValueError('Transactional executor required')
        self.executor,self.scope,self.transcript=executor,scope,transcript
        self.current,self.guard,self.authorize=current,lifecycle_guard,authorize
        self.max_bytes=max_bytes
        self._pins=(executor,scope,transcript,transcript.store,transcript.store.root,transcript.agent_id,current,lifecycle_guard,authorize,max_bytes)
    def _check(self,*,cleanup_check=None):
        e,s,t,store,root,agent,current,guard,authorize,bound=self._pins
        def pins():
            if self.executor is not e or self.scope is not s or self.transcript is not t or t.store is not store or store.root!=root or t.agent_id!=agent or self.current is not current or self.guard is not guard or self.authorize is not authorize or self.max_bytes!=bound:
                raise ProjectionUnavailable('Projection resource changed')
        pins()
        if (cleanup_check() if cleanup_check is not None else current(s)) is not True:raise ProjectionUnavailable('Projection lifetime changed')
        pins()
    def project(self,role,scope,outcome,identity,*,cleanup=None):
        if role not in ('goals','planner','worker') or not isinstance(outcome,ChatOutcome) or outcome.persisted is not True or outcome.stale:
            raise ProjectionUnavailable('Confirmed terminal turn required')
        identity=_scope_identity(identity)  # bounded canonical snapshot before any callback
        cleanup_check=None
        if cleanup is not None:
            from .inflight_cleanup import CleanupTurn,InflightCleanupOwner
            if type(cleanup) is not CleanupTurn or type(cleanup.owner) is not InflightCleanupOwner:
                raise ProjectionUnavailable('Exact registered cleanup capability required')
            owner=cleanup.owner
            def cleanup_check():
                if cleanup.owner is not owner:raise ProjectionUnavailable('Cleanup owner changed')
                return owner.projection_current(cleanup,self,role,scope,outcome,identity)
        def check():self._check(cleanup_check=cleanup_check)
        base=self.scope
        if any(getattr(scope,k)!=getattr(base,k) for k in ('task_id','exploration_id','run_id','revision')):
            raise ProjectionUnavailable('Foreign projection scope')
        if any(type(identity.get(k)) is not type(getattr(base,k)) or identity.get(k)!=getattr(base,k) for k in ('task_id','exploration_id','run_id','revision')) or identity.get('role')!=role:
            raise ProjectionUnavailable('Foreign transcript identity')
        node=scope.intent_id if role=='worker' else None
        session=scope.session_id
        attempted=False
        try:
            with self._pins[7](base):
                check()
                receipt=self.transcript.finalized_scoped_result(session,outcome.turn.id,identity)
                if receipt is None or receipt.run_id!=outcome.turn.run_id:raise ProjectionUnavailable('Durable scoped receipt unavailable')
                result=receipt.result
                proposal=ProjectionProposal(base.task_id,base.exploration_id,base.run_id,base.revision,role,node,receipt.turn_id,receipt.result_digest)
                def grant():
                    check();g=self.authorize(proposal);check()
                    if not isinstance(g,Grant) or any(v is not True for v in (g.permission,g.in_scope,g.approved)):raise ProjectionUnavailable('Progress projection not authorized')
                grant()
                key=hashlib.sha256(json.dumps([session,receipt.turn_id,identity],sort_keys=True,allow_nan=False).encode()).hexdigest()
                marker={'key':key,'digest':receipt.result_digest,'turn_id':receipt.turn_id,'role':role,'run_id':base.run_id,'revision':base.revision}
                # A zero-output turn must not reuse an earlier assistant's text.
                generated=deepcopy(result.messages[receipt.prefix_length:])
                detail=RunResult(result.reason,generated,result.usage,result.turns,result.tool_calls).text
                if len(detail.encode('utf-8'))>self.max_bytes:raise ProjectionUnavailable('Progress result exceeds bound')
                item=Activity(worker='planner' if role in ('goals','planner') else scope.owner,node_id=node,kind='result',
                    summary=detail.split('\n',1)[0][:400],detail=detail,is_error=result.reason not in ('completed','complete','end_turn','stop'),
                    metadata={'_artex_projection':marker})
                for name in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'):
                    if name in result.usage:setattr(item,name,result.usage[name])
                with self.executor.transaction() as tx:
                    row=tx.fetch_one(Query('SELECT exploration_id,deleted_at FROM tasks WHERE id=$1 FOR UPDATE',(base.task_id,)))
                    if row is None or row[0]!=base.exploration_id or row[1] is not None:raise ProjectionUnavailable('Task disappeared')
                    fields=_values(item)
                    # Compare canonical persisted payload inside SQL, not merely
                    # bookkeeping. No possibly edited detail body is disclosed.
                    rows=tx.fetch_all(Query("""SELECT id,
(metadata->'_artex_projection'=$3::jsonb
AND node_id IS NOT DISTINCT FROM $4::bigint
AND worker IS NOT DISTINCT FROM NULLIF($5,'')
AND kind IS NOT DISTINCT FROM NULLIF($6,'')
AND tool IS NOT DISTINCT FROM NULLIF($7,'')
AND tool_use_id IS NOT DISTINCT FROM NULLIF($8,'')
AND is_error IS NOT DISTINCT FROM $9::boolean
AND summary IS NOT DISTINCT FROM NULLIF($10,'')
AND detail IS NOT DISTINCT FROM NULLIF($11,'')
AND input_tokens IS NOT DISTINCT FROM $12::integer
AND output_tokens IS NOT DISTINCT FROM $13::integer
AND cache_read_tokens IS NOT DISTINCT FROM $14::integer
AND cache_write_tokens IS NOT DISTINCT FROM $15::integer
AND main_seg IS NOT DISTINCT FROM $16::integer) AS payload_matches
FROM activity WHERE exploration_id=$1 AND metadata->'_artex_projection'->>'key'=$2
ORDER BY id LIMIT 2""",(base.exploration_id,key,json.dumps(marker),*fields[:8],*fields[9:])))
                    grant()
                    if rows:
                        if len(rows)!=1 or rows[0][1] is not True:raise ProjectionUnavailable('Conflicting projection receipt')
                        ids=[rows[0][0]]
                    else:
                        store=TaskActivityStore(tx,base.task_id,base.exploration_id,lifecycle_guard=lambda:nullcontext(),current=lambda:(grant() is None),scope_identity=identity)
                        attempted=True;ids=store.append_many([item])
                    grant()
                grant();return tuple(ids)
        except BaseException:
            if attempted:raise ProjectionUncertain('Progress persistence unconfirmed; reconcile receipt without rerunning execution') from None
            raise
