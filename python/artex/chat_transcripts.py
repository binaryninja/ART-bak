"""Durable ChatAgent adapter over the existing Norma JSONL TranscriptStore.

Explicit store/root only. Uses that store's POSIX session lock, no-follow read,
atomic replacement and fsync primitives, including for metadata-only updates.
No second transcript format or extra control records are introduced. Ordinary
Norma message fields stay unchanged; admission records carry the namespaced extra
`_artex_chat` (schema=1, integer run_id/version, client ID, content digests, phase).
Completion atomically updates only that extra metadata and appends the complete
assistant/tool-result batch. Zero-output turns need no synthetic message/boundary.
Whole-turn usage is attached to the final generated record, or admission record
when there is no generated message. Unknown pre-existing record fields survive.

TranscriptTurn.revision is the SHA256 content revision encoded as an opaque
nonnegative integer; metadata version is 1 for running, 2 for finalized. Every
loaded record/metadata/digest is validated before it grants turn authority.
Exact original bytes fence external append/clear/replacement. All writers must
honor TranscriptStore's locks. Compaction, delete lifecycles and importing hostile
root directories are outside this adapter. Identifiers follow TranscriptStore's
narrow alphanumeric/underscore/hyphen filename contract, not all ChatAgent IDs.

Unresolved admission (including duplicate client ID) raises ChatBusy. Only verified
finalized replays return created=False, finalized=True. Failed writes may already
have renamed: reread/reconcile matching terminal digests, NEVER rerun inference.
Interrupted recovery requires an explicit authoritative recovery_guard held through
commit, asserting no old execution can still act. It records unknown effects and
never retries tools/models. No default recovery, directories, threads or providers.
"""
from copy import deepcopy
from dataclasses import asdict,dataclass
from datetime import datetime,timezone
import hashlib
import json
import re
import time
import uuid
from .transcripts import TranscriptStore,TranscriptError,TranscriptConflict,TranscriptUnavailable,_encode,_decode,messages
from .agent.chat import TranscriptTurn,ChatBusy
from .agent.runtime import RunResult
from .providers import Config,serialize

META='_artex_chat'
_CLIENT=re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')


def _clone(value):return _decode(_encode(value))
def _digest(value):return hashlib.sha256(_encode(value)).hexdigest()
def _integer(value,minimum=0):return type(value) is int and minimum<=value<2**63

def _scope_identity(value):
    if not isinstance(value,dict) or not value:raise TranscriptError('Nonempty scope identity required')
    def check(v):
        if isinstance(v,dict):
            if any(not isinstance(k,str) for k in v):raise ValueError()
            for child in v.values():check(child)
        elif isinstance(v,list):
            for child in v:check(child)
        elif v is not None and type(v) not in (str,int,float,bool):raise ValueError()
    try:
        check(value)
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        if len(raw)>65536:raise ValueError()
        return json.loads(raw)
    except (ValueError,TypeError,UnicodeError,RecursionError):raise TranscriptError('Invalid scope identity') from None


def _history(records):
    result,_=messages(records)
    try:serialize(Config('openai','history-validation'),{'messages':result})
    except Exception:raise TranscriptError('Invalid or unpaired transcript messages') from None
    return _clone(result)


def _result_value(result):
    if not isinstance(result,RunResult) or not isinstance(result.reason,str) or not result.reason:
        raise TranscriptError('A complete run result is required')
    if not _integer(result.turns) or not _integer(result.tool_calls):raise TranscriptError('Invalid run counters')
    if not isinstance(result.usage,dict) or any(not isinstance(k,str) or not _integer(v) for k,v in result.usage.items()):
        raise TranscriptError('Invalid run usage')
    if not isinstance(result.messages,list) or not isinstance(result.events,list):raise TranscriptError('Invalid run result')
    value=_clone(asdict(result))
    try:serialize(Config('openai','history-validation'),{'messages':value['messages']})
    except Exception:raise TranscriptError('Incomplete or invalid tool-message batch') from None
    return value


@dataclass(frozen=True)
class FinalizedScopedResult:
    """Verified read receipt, not authority to run or retry any effects.

    prefix_length counts canonical messages through the admitted user message.
    result is an independent value copy; mutating it cannot alter persisted data.
    """
    turn_id:str
    run_id:int
    prefix_length:int
    result_digest:str
    result:RunResult


class ChatTranscriptAdapter:
    def __init__(self,store,*,agent_id='',recovery_guard=None):
        if not isinstance(store,TranscriptStore):raise TypeError('Explicit TranscriptStore required')
        if recovery_guard is not None and not callable(recovery_guard):raise TypeError('Recovery guard must be callable')
        self.store=store;self.agent_id=agent_id;self.recovery_guard=recovery_guard

    def _validate(self,session,records):
        ids=set();clients=set();turns={};last_run=0
        for index,record in enumerate(records):
            if (not isinstance(record,dict) or record.get('session_id')!=session
                or record.get('agent_id','')!=self.agent_id or record.get('type') not in ('message','boundary')
                or not isinstance(record.get('uuid'),str) or not record['uuid'] or record['uuid'] in ids):
                raise TranscriptError('Invalid transcript record identity')
            ids.add(record['uuid'])
            if self.agent_id and record.get('is_sidechain') is not True:raise TranscriptError('Invalid sidechain record')
            try:
                timestamp=datetime.fromisoformat(record['timestamp'].replace('Z','+00:00'))
                if timestamp.tzinfo is None:raise ValueError()
            except (KeyError,TypeError,ValueError,AttributeError):raise TranscriptError('Invalid transcript timestamp') from None
            meta=record.get(META)
            if meta is None:continue
            if (not isinstance(meta,dict) or meta.get('schema')!=1 or type(meta.get('schema')) is not int
                or meta.get('phase') not in ('running','finalized')
                or meta.get('turn_id')!=record['uuid'] or not _integer(meta.get('run_id'),1)
                or meta['run_id']<=last_run or not isinstance(meta.get('client_id'),str)
                or not _CLIENT.fullmatch(meta['client_id']) or meta['client_id'] in clients
                or record.get('type')!='message' or not isinstance(record.get('message'),dict)
                or record['message'].get('role')!='user'):
                raise TranscriptError('Invalid chat admission metadata')
            keys={'schema','version','phase','turn_id','client_id','run_id','user_digest','prefix_digest'}
            if 'scope_identity' in meta:
                keys|={'scope_identity','scope_digest'}
                if meta.get('scope_digest')!=_digest(_scope_identity(meta['scope_identity'])):
                    raise TranscriptError('Scope identity digest mismatch')
            if meta['phase']=='finalized':keys|={'result','result_digest','generated_count'}
            if set(meta)!=keys:raise TranscriptError('Unknown or missing chat metadata fields')
            last_run=meta['run_id'];clients.add(meta['client_id'])
            if meta.get('version')!=(1 if meta['phase']=='running' else 2) or type(meta.get('version')) is not int:
                raise TranscriptError('Invalid chat metadata version')
            prefix=_history(records[:index+1])
            if meta.get('user_digest')!=_digest(record['message']) or meta.get('prefix_digest')!=_digest(prefix):
                raise TranscriptError('Chat admission content digest mismatch')
            if meta['phase']=='running':
                if any(key in meta for key in ('result','result_digest','generated_count')):raise TranscriptError('Invalid running metadata')
            else:
                count=meta.get('generated_count')
                if not _integer(count) or index+count>=len(records):raise TranscriptError('Invalid finalized range')
                output=records[index+1:index+1+count]
                if any(r.get('type')!='message' or META in r for r in output):raise TranscriptError('Invalid finalized message range')
                details=meta.get('result')
                if not isinstance(details,dict) or 'messages' in details:raise TranscriptError('Invalid terminal metadata')
                try:value=_result_value(RunResult(messages=_history(records[:index+1+count]),**details))
                except (TypeError,ValueError):raise TranscriptError('Invalid terminal result') from None
                if value['messages'][:len(prefix)]!=prefix or meta.get('result_digest')!=_digest(value):
                    raise TranscriptError('Finalized result digest mismatch')
            turns[meta['client_id']]=(index,meta,prefix)
        if sum(meta['phase']=='running' for _,meta,_ in turns.values())>1:
            raise TranscriptError('Multiple unresolved admissions')
        _history(records)
        return turns

    def _turn(self,records,index,meta,prefix,*,created):
        # Reconstruct exact canonical admission bytes, removing terminal-only
        # metadata/usage changes if this is an idempotent finalized replay.
        admission=deepcopy(records[:index+1]);record=admission[-1]
        if meta['phase']=='finalized':
            record[META]={k:v for k,v in meta.items() if k not in ('result','result_digest','generated_count')}
            record[META]['phase']='running';record[META]['version']=1
            if meta['generated_count']==0:record.pop('usage',None)
        raw=b''.join(_encode(r)+b'\n' for r in admission)
        return TranscriptTurn(meta['turn_id'],int(hashlib.sha256(raw).hexdigest(),16),meta['run_id'],
            deepcopy(prefix),created=created,finalized=meta['phase']=='finalized')

    def begin(self,session_id,client_id,user_message):
        return self._begin(session_id,client_id,user_message,None)

    def begin_scoped(self,session_id,client_id,user_message,scope_identity):
        """Persist opaque, nonsecret host scope identity; reject cross-scope reuse.

        Scope is never sent to the model. Existing unscoped admissions cannot be
        retroactively treated as scoped. Digests detect corruption, not an attacker
        able to rewrite the entire trusted transcript store.
        """
        return self._begin(session_id,client_id,user_message,_scope_identity(scope_identity))

    def check_scoped(self,session_id,client_id,user_message,scope_identity):
        """Read-only preflight; begin_scoped must still recheck atomically."""
        identity=_scope_identity(scope_identity)
        if not isinstance(client_id,str) or not _CLIENT.fullmatch(client_id):raise ValueError('Invalid client request id')
        user=_clone(user_message)
        if not isinstance(user,dict) or user.get('role')!='user':raise ValueError('User message required')
        with self.store._locked(session_id,self.agent_id) as (directory,name):
            _,records=self.store._load(directory,name)
            turns=self._validate(session_id,records)
            if client_id in turns:
                index,meta,_=turns[client_id]
                if meta.get('scope_identity')!=identity or records[index]['message']!=user:
                    raise TranscriptConflict('Client request id reused for different scope or input')
                if meta['phase']=='running':raise ChatBusy('Prior turn is unresolved')
            elif any(meta['phase']=='running' for _,meta,_ in turns.values()):
                raise ChatBusy('Prior turn is unresolved')
        return None

    def _begin(self,session_id,client_id,user_message,scope_identity):
        if not isinstance(client_id,str) or not _CLIENT.fullmatch(client_id):raise ValueError('Invalid client request id')
        user=_clone(user_message)
        if not isinstance(user,dict) or user.get('role')!='user':raise ValueError('User message required')
        with self.store._locked(session_id,self.agent_id) as (directory,name):
            raw,records=self.store._load(directory,name)
            turns=self._validate(session_id,records)
            if client_id in turns:
                index,meta,prefix=turns[client_id]
                if meta.get('scope_identity')!=scope_identity:raise TranscriptConflict('Client request id reused for different scope')
                if records[index]['message']!=user:raise TranscriptConflict('Client request id reused for changed input')
                if meta['phase']=='running':raise ChatBusy('Prior turn is unresolved; reconcile instead of re-executing')
                return self._turn(records,index,meta,prefix,created=False)
            if any(meta['phase']=='running' for _,meta,_ in turns.values()):raise ChatBusy('Prior turn is unresolved')
            history=_history(records)+[user]
            _result_value(RunResult('admission',history,{},0,0,[]))
            run_id=max(time.time_ns(),max((meta['run_id'] for _,meta,_ in turns.values()),default=0)+1)
            if not _integer(run_id,1):raise TranscriptError('Run identity exhausted')
            turn_id=uuid.uuid4().hex
            meta={'schema':1,'version':1,'phase':'running','turn_id':turn_id,'client_id':client_id,
                  'run_id':run_id,'user_digest':_digest(user),'prefix_digest':_digest(history)}
            if scope_identity is not None:
                meta.update(scope_identity=scope_identity,scope_digest=_digest(scope_identity))
            record=self._record(session_id,turn_id,records[-1].get('uuid','') if records else '')
            record.update(type='message',message=user);record[META]=meta
            updated=records+[record]
            revision=self.store._write(directory,name,updated)
            return TranscriptTurn(turn_id,int(revision,16),run_id,history,created=True,finalized=False)

    def _record(self,session,id,parent):
        record={'uuid':id,'session_id':session,'timestamp':datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
        if parent:record['parent_uuid']=parent
        if self.agent_id:record.update(agent_id=self.agent_id,is_sidechain=True)
        return record

    def _find_turn(self,session,records,turn):
        if (not isinstance(turn,TranscriptTurn) or not isinstance(turn.id,str)
            or type(turn.revision) is not int or turn.revision<0 or not _integer(turn.run_id,1)):
            raise TranscriptError('Invalid transcript turn')
        turns=self._validate(session,records)
        for index,meta,prefix in turns.values():
            if meta['turn_id']==turn.id:
                original=self._turn(records,index,meta,prefix,created=False)
                if original.revision!=turn.revision or meta['run_id']!=turn.run_id or prefix!=turn.messages:
                    raise TranscriptConflict('Turn identity or history changed')
                return index,meta,prefix
        return None

    def current(self,session_id,turn):
        snapshot=self.store.begin(session_id,self.agent_id)
        found=self._find_turn(session_id,list(snapshot.records),turn)
        return bool(found and found[1]['phase']=='running' and int(snapshot.revision,16)==turn.revision)

    def commit(self,session_id,turn,result):
        value=_result_value(result)
        with self.store._locked(session_id,self.agent_id) as (directory,name):
            raw,records=self.store._load(directory,name)
            found=self._find_turn(session_id,records,turn)
            if not found:return False
            index,meta,prefix=found
            if value['messages'][:len(prefix)]!=prefix:raise TranscriptConflict('Run rewrote admitted history')
            digest=_digest(value)
            if meta['phase']=='finalized':
                if meta['result_digest']!=digest:raise TranscriptConflict('Final result differs from persisted result')
                return True
            if int(self.store._revision(raw),16)!=turn.revision:return False
            updated=deepcopy(records)
            generated=value['messages'][len(prefix):]
            parent=records[-1]['uuid']
            for message in generated:
                record=self._record(session_id,uuid.uuid4().hex,parent);record.update(type='message',message=message)
                updated.append(record);parent=record['uuid']
            updated[-1]['usage']=deepcopy(value['usage'])
            terminal=dict(meta,version=2,phase='finalized',generated_count=len(generated),result_digest=digest,
                result={k:v for k,v in value.items() if k!='messages'})
            updated[index][META]=terminal
            self.store._write(directory,name,updated)
            return True

    def finalized_result(self,session_id,turn):
        """Read a digest-verified durable result; unresolved/missing turns return None."""
        snapshot=self.store.begin(session_id,self.agent_id);records=list(snapshot.records)
        found=self._find_turn(session_id,records,turn)
        if found is None or found[1]['phase']!='finalized':return None
        index,meta,prefix=found
        result=RunResult(messages=_history(records[:index+1+meta['generated_count']]),**deepcopy(meta['result']))
        value=_result_value(result)
        if _digest(value)!=meta['result_digest']:raise TranscriptError('Finalized result digest mismatch')
        return RunResult(**value)

    def finalized_scoped_result(self,session_id,turn_id,scope_identity):
        """Read exact durable scoped turn without requiring its original prefix.

        Missing/running turns return None. A matching ID with another scope (or
        legacy unscoped metadata) conflicts, never grants cross-run replay. All
        records and message pairing/digests are validated, including later turns.
        No admission, metadata recovery or transcript-record writes are performed.
        Existing store locking may create lock files or agent directories. Digests detect
        corruption, not malicious rewriting of a fully attacker-owned store.
        """
        if not isinstance(turn_id,str) or not re.fullmatch(r'[0-9a-f]{32}',turn_id):
            raise TranscriptError('Invalid scoped turn ID')
        identity=_scope_identity(scope_identity)
        snapshot=self.store.begin(session_id,self.agent_id)
        records=list(snapshot.records)
        turns=self._validate(session_id,records)
        for index,meta,prefix in turns.values():
            if meta['turn_id']!=turn_id:continue
            if meta.get('scope_digest')!=_digest(identity):
                raise TranscriptConflict('Turn scope identity differs')
            if meta['phase']!='finalized':return None
            value=_result_value(RunResult(messages=_history(records[:index+1+meta['generated_count']]),**deepcopy(meta['result'])))
            if _digest(value)!=meta['result_digest']:
                raise TranscriptError('Finalized result digest mismatch')
            return FinalizedScopedResult(turn_id,meta['run_id'],len(prefix),meta['result_digest'],RunResult(**value))
        return None

    def pending(self,session_id):
        """Inspection/recovery only; never a permission to resume model/tool execution."""
        snapshot=self.store.begin(session_id,self.agent_id);records=list(snapshot.records)
        turns=self._validate(session_id,records)
        pending=[self._turn(records,i,m,p,created=False) for i,m,p in turns.values() if m['phase']=='running']
        if len(pending)>1:raise TranscriptError('Multiple unresolved admissions')
        return pending[0] if pending else None

    def recover_interrupted(self,session_id,turn):
        if self.recovery_guard is None:raise TranscriptUnavailable('Authoritative stopped-writer recovery guard required')
        with self.recovery_guard(session_id,turn):
            return self.commit(session_id,turn,RunResult('interrupted_unknown_effects',deepcopy(turn.messages),{},0,0,[]))
