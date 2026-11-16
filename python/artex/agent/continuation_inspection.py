"""Ephemeral read-only paused-handoff observations, never release/recovery authority.

SELECT observations depend on the cooperative host guard. They are not durable
fencing, proof of crash safety or permission to discard an original capability.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from .task_continuation import TaskContinuationOwner,Suspension,ContinuationProposal
from .bootstrap_handle import BootstrapHandle
from .engine import TaskEngineHandle
from .inflight_cleanup import InflightCleanupOwner
from ..manager import TaskManager
from ..db.core import Query

_OWNER_NAMES=('validate_bindings','_record','_allow','_registry')
_OWNER_METHODS=tuple(getattr(TaskContinuationOwner,n) for n in _OWNER_NAMES)
_HANDLE_BINDING=BootstrapHandle._binding
_HANDLE_RECEIPT=BootstrapHandle.validate_prepared_receipt
_MANAGER_NAMES=("_continuation_identity","_has_cleanup")
_MANAGER_METHODS=tuple(getattr(TaskManager,n) for n in _MANAGER_NAMES)
_CLEANUP_BINDING=InflightCleanupOwner._binding
_REGISTRY=TaskManager.validate_suspension

_SQL="""SELECT task.updated_at,journal.run_id,journal.revision,
 NOT EXISTS(SELECT 1 FROM exploration_nodes node
 WHERE node.exploration_id=task.exploration_id AND node.kind='intent' AND node.state='running')
 FROM tasks task JOIN public.artex_task_bootstrap journal ON journal.task_id=task.id
 WHERE task.id=$1 AND task.exploration_id=$2 AND task.status='running' AND task.paused
 AND NOT task.queued AND task.timeout_seconds=0 AND task.first_run_at IS NULL
 AND task.deadline_at IS NULL AND task.deleted_at IS NULL AND task.archived_at IS NULL
 AND NOT EXISTS(SELECT 1 FROM task_archives archive WHERE archive.task_id=task.id)
 AND NOT EXISTS(SELECT 1 FROM task_relations relation WHERE relation.task_id=task.id)
 AND journal.exploration_id=$2 AND journal.run_id=$3 AND journal.request_id=$4
 AND journal.config_digest=$5 AND journal.owner_token=$6 AND journal.revision=$7
 AND journal.phase='prepared' LIMIT 2"""

@dataclass(frozen=True)
class HandoffObservation:
    task_id:str
    state:str
    reasons:tuple[str,...]
    evidence:tuple[str,...]=()
    task_version:str|None=None
    bootstrap_run_id:int|None=None
    bootstrap_revision:int|None=None
    recoverable:bool=False
    release_authorized:bool=False

class _Ineligible(Exception):pass

class PausedHandoffInspector:
    def __init__(self,owner,*,authorize):
        if type(owner) is not TaskContinuationOwner or not callable(authorize):raise ValueError('Original owner and explicit inspection authority required')
        self.owner,self.authorize=owner,authorize
        self._pins=(owner,authorize,owner.application,owner.manager,owner.runner,owner.runtime.executor,owner._pins[8])
        self._check()
    def _check(self):
        o,a,app,m,r,e,g=self._pins
        if self.owner is not o or self.authorize is not a or o.application is not app or o.manager is not m or o.runner is not r or o.runtime.executor is not e or o._pins[8] is not g:raise RuntimeError('Inspection binding changed')
        for name,method in zip(_OWNER_NAMES,_OWNER_METHODS):
            bound=getattr(o,name)
            if getattr(bound,'__self__',None) is not o or getattr(bound,'__func__',None) is not method:raise RuntimeError('Original owner methods required')
        for name,method in zip(_MANAGER_NAMES,_MANAGER_METHODS):
            bound=getattr(m,name)
            if getattr(bound,'__self__',None) is not m or getattr(bound,'__func__',None) is not method:raise RuntimeError('Original manager methods required')
        if m._continuation_owner is not o or r._continuation_owner is not o or not m._continuation_identity(o):raise RuntimeError('Original installed owner required')
        o.validate_bindings()
    def _authorize(self,cap):
        self._check()
        if self._pins[1](cap.task_id) is not True:raise PermissionError('Handoff inspection denied')
        self._check()
    def _owner_authorize(self,cap):
        # Authority-only check also gates negative diagnostics. Do not require
        # clean eligibility here, since quarantine/unsealed are valid negatives.
        self._check();o=self.owner
        with o._lock:
            rec=o._records.get(cap)
            if cap.owner is not o or rec is None:raise PermissionError('Handoff inspection denied')
            old=rec['old'];receipt=rec['receipt']
            if cap.task_id!=old.task_id or old._receipt is not receipt:raise PermissionError('Handoff inspection denied')
            proposal=ContinuationProposal(receipt.task_id,receipt.exploration_id,receipt.run_id,old.policy_revision,'inspect_handoff')
        if o._pins[6](proposal) is not True:raise PermissionError('Handoff inspection denied')
        self._check()
        with o._lock:
            if o._records.get(cap) is not rec or rec['receipt'] is not receipt or rec['old'] is not old:raise PermissionError('Handoff inspection denied')
    def _disclose(self,cap,result):
        # Finite authority-only sweep after I/O. The last inspection observer
        # precedes the owner grant so it cannot revoke an already-observed owner.
        self._authorize(cap);self._owner_authorize(cap);self._check()
        return result
    def _local(self,cap):
        self._check();o=self.owner;m=o.manager;r=o.runner
        with o.host._condition:
            if o.host._mutations or o.host._working or o.host._settling or o.host._intent_control is not None:raise _Ineligible('host_lifecycle_busy')
        with o._lock:
            rec=o._records.get(cap)
            if rec is None or cap.owner is not o:raise _Ineligible('original_capability_missing')
            if not o._resume_sealed:raise _Ineligible('resume_not_sealed')
            if o._resume_attempts:raise _Ineligible('resume_attempts_active')
            if rec['failed'] or rec['state']!='paused':raise _Ineligible('suspension_not_clean_paused')
            old=rec['old'];receipt=rec['receipt'];stamp=rec['stamp']
        if o._record(cap) is not rec:raise _Ineligible('original_capability_changed')
        if type(old) is not BootstrapHandle or getattr(old._binding,'__self__',None) is not old or getattr(old._binding,'__func__',None) is not _HANDLE_BINDING:raise _Ineligible('original_handle_missing')
        if getattr(old.validate_prepared_receipt,'__self__',None) is not old or getattr(old.validate_prepared_receipt,'__func__',None) is not _HANDLE_RECEIPT:raise _Ineligible('original_handle_missing')
        _HANDLE_BINDING(old)
        child=old._child
        if type(child) is not TaskEngineHandle or child.process is not None or child._process is not None:raise _Ineligible('process_or_engine_unsupported')
        with old._condition:
            if old._active or old._unresolved or old._phase!='engine' or not old.token.cancelled:raise _Ineligible('generation_not_drained')
        with child._condition:
            if child._active or child._unresolved or child._intent_control is not None:raise _Ineligible('engine_not_drained')
        cleanup=child.cleanup
        if type(cleanup) is not InflightCleanupOwner or cleanup is not child._cleanup_owner or getattr(cleanup._binding,'__self__',None) is not cleanup or getattr(cleanup._binding,'__func__',None) is not _CLEANUP_BINDING:raise _Ineligible('canonical_cleanup_missing')
        _CLEANUP_BINDING(cleanup)
        with cleanup._lock:
            if cleanup._records:raise _Ineligible('cleanup_pending')
        with m._lock:
            if not _REGISTRY(m,cap,old,old.token,'paused'):raise _Ineligible('paused_registry_changed')
            slot=m._suspensions.get(cap.task_id)
            if slot is None or slot.get('credit') is not None or cap.task_id in m._deadline_slots or cap.task_id in m._unstamped_slots or m._has_cleanup(cap.task_id):raise _Ineligible('other_ownership_pending')
        with r._lock:
            if r._handles.get(cap.task_id) is not old or cap.task_id in r._frontier or old in r._final_retirement:raise _Ineligible('runner_ownership_changed')
        if type(stamp) is not datetime or type(stamp.tzinfo) is not timezone:raise _Ineligible('paused_version_missing')
        return rec,receipt,stamp
    def _database(self,tx,receipt,stamp):
        args=(receipt.task_id,receipt.exploration_id,receipt.run_id,receipt.request_id,receipt.config_digest,receipt.owner_token,receipt.revision)
        rows=tx.fetch_all(Query(_SQL,args))
        if type(rows) not in (tuple,list) or len(rows)!=1 or type(rows[0]) not in (tuple,list) or len(rows[0])!=4:raise _Ineligible('durable_state_mismatch')
        version,run,revision,no_running=rows[0]
        if type(version) is not datetime or type(version.tzinfo) is not timezone or version!=stamp or type(run) is not int or type(revision) is not int or (run,revision)!=(receipt.run_id,receipt.revision) or no_running is not True:raise _Ineligible('durable_state_mismatch')
        return version.isoformat(),run,revision
    def inspect(self,cap):
        if type(cap) is not Suspension or type(cap.task_id) is not str or not cap.task_id.isascii() or not cap.task_id.isdecimal() or not 0<len(cap.task_id)<=19 or not 0<int(cap.task_id)<2**63:raise ValueError('Exact bounded suspension required')
        self._authorize(cap);self._owner_authorize(cap)
        try:
            rec,receipt,stamp=self._local(cap)
            with self._pins[5].transaction() as tx:
                with self._pins[6](receipt.task_id,receipt.exploration_id):
                    self.owner._allow(cap,'inspect_handoff');self._authorize(cap)
                    current=self._local(cap)
                    if current[0] is not rec or current[1] is not receipt or current[2]!=stamp:raise _Ineligible('local_evidence_changed')
                    self._database(tx,receipt,stamp)
                # Reobserve after guard exits and final policy callbacks. SELECT
                # remains bounded and no lock/CAS or durable authority is created.
                self.owner._allow(cap,'inspect_handoff');self._authorize(cap)
                current=self._local(cap)
                if current[0] is not rec or current[1] is not receipt or current[2]!=stamp:raise _Ineligible('local_evidence_changed')
                evidence=self._database(tx,receipt,stamp)
            self._check()
            current=self._local(cap)
            if current[0] is not rec or current[1] is not receipt or current[2]!=stamp:raise _Ineligible('local_evidence_changed')
            result=HandoffObservation(cap.task_id,'observed_prerequisites_complete',(),
                ('original_local_capability','resume_sealed','no_active_resume_attempts','clean_paused_registry',
                 'cancelled_drained_generation','canonical_cleanup_empty','paused_version_matches',
                 'bootstrap_generation_matches','no_running_intents_observed','durable_handoff_protocol_absent'),*evidence)
        except PermissionError:raise
        except _Ineligible as e:result=HandoffObservation(cap.task_id,'ineligible',(str(e),))
        except Exception:result=HandoffObservation(cap.task_id,'unavailable',('inspection_unavailable',))
        result=self._disclose(cap,result)
        if result.state=='observed_prerequisites_complete':
            try:
                current=self._local(cap)
                if current[0] is not rec or current[1] is not receipt or current[2]!=stamp:raise _Ineligible('local_evidence_changed')
            except _Ineligible as e:return HandoffObservation(cap.task_id,'ineligible',(str(e),))
            except Exception:return HandoffObservation(cap.task_id,'unavailable',('inspection_unavailable',))
        return result
