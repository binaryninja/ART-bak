"""Parent-bound persistence from db/side_questions.go; no provider/tool execution.

Lock order is real parent(s), then session. Snapshot run/version ordering, request
idempotency, generation and sequence CAS follow source PostgreSQL contracts.
Additional boundary checks reject ambiguous parents, cross-parent DTOs, malformed
JSON/identities, and history limits outside 1..100 (HTTP uses 21). request(id)
also scopes by parent; update additionally checks the caller's generation.

clear_history implements source's permanent request deletion and generation bump;
calling hosts must obtain required deletion approval. Tests use inert executors.
Archive export/restore, runtime capture, cancellation orchestration and provider
answering are NOT implemented. Existing parent soft-delete/archive columns and
foreign-key cascades are honored, not replaced with an in-memory approximation.
No live PostgreSQL/concurrency guarantee is claimed from contract tests alone.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
import uuid
from .core import Query, int64


class SideInvalid(ValueError): pass
class SideBusy(RuntimeError): pass
class SideParentGone(LookupError): pass
class SideSessionMissing(LookupError): pass


def _number(value, name, minimum=0):
    try: value = int64(value, name)
    except ValueError: raise SideInvalid(f'invalid {name}') from None
    if value < minimum: raise SideInvalid(f'invalid {name}')
    return value


def _text(value, name='text'):
    if not isinstance(value, str) or '\x00' in value: raise SideInvalid(f'invalid {name}')
    try: value.encode('utf-8')
    except UnicodeError: raise SideInvalid(f'invalid {name}') from None
    return value


def _json(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        raw.encode('utf-8')
        # PostgreSQL jsonb cannot store NUL, as in upstream jsonbClean.
        def clean(item):
            if isinstance(item, str): return item.replace('\x00', '')
            if isinstance(item, list): return [clean(v) for v in item]
            if isinstance(item, dict): return {clean(k): clean(v) for k, v in item.items()}
            return item
        return json.dumps(clean(json.loads(raw)), ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SideInvalid('invalid JSON payload') from None


def _object(raw):
    try:
        value=json.loads(raw) if isinstance(raw,(str,bytes,bytearray)) else raw
        if not isinstance(value,dict): raise ValueError
        return json.loads(_json(value))
    except (ValueError,TypeError): raise SideInvalid('invalid stored JSON object') from None


def _time(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SideInvalid('timestamp must have a timezone')
    return value


@dataclass(frozen=True)
class Parent:
    conversation_id: int = 0
    task_id: int = 0
    exploration_id: int = 0
    intent_id: int = 0

    def __post_init__(self):
        for name,value in asdict(self).items(): _number(value,name)
        if self.conversation_id:
            if self.task_id or self.exploration_id or self.intent_id: raise SideInvalid('ambiguous side parent')
        elif not self.task_id or not self.exploration_id: raise SideInvalid('task and exploration are required')

    def key(self):
        if self.conversation_id: return f'conv-{self.conversation_id}'
        suffix=f'worker-i{self.intent_id}' if self.intent_id else 'main'
        return f'task-{self.task_id}-exp-{self.exploration_id}-{suffix}'

    def to_dict(self): return {k:v for k,v in asdict(self).items() if v}


@dataclass(frozen=True)
class Model:
    profile_id: int = 0
    name: str = ''
    format: str = ''
    model: str = ''
    identity: str = ''
    streaming: bool = False
    window_tokens: int = 0

    def to_dict(self):
        _number(self.profile_id,'profile id'); _number(self.window_tokens,'window tokens')
        for name in ('name','format','model','identity'): _text(getattr(self,name),name)
        if type(self.streaming) is not bool: raise SideInvalid('streaming must be boolean')
        return asdict(self)


@dataclass(frozen=True)
class Snapshot:
    parent: Parent
    run_id: int
    version: int
    captured_at: datetime
    model: Model = field(default_factory=Model)
    request: dict = field(default_factory=dict)

    def to_dict(self):
        if not isinstance(self.parent,Parent) or not isinstance(self.model,Model): raise SideInvalid('invalid snapshot identity')
        _number(self.run_id,'run id'); _number(self.version,'version')
        if not isinstance(self.request,dict): raise SideInvalid('request must be an object')
        return {'parent':self.parent.to_dict(),'run_id':self.run_id,'version':self.version,
                'captured_at':_time(self.captured_at).isoformat(),'model':self.model.to_dict(),'request':self.request}


@dataclass
class Exchange:
    id: str
    ordinal: int
    session_key: str
    generation: int
    client_id: str
    question: str
    answer: str
    status: str
    error: str
    model: Model
    snapshot_at: datetime
    created_at: datetime
    sequence: int
    usage: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)

    def to_dict(self):
        result={'id':self.id,'ordinal':self.ordinal,'client_request_id':self.client_id,
                'question':self.question,'answer':self.answer,'status':self.status,
                'model':self.model.to_dict(),'snapshot_at':_time(self.snapshot_at).isoformat(),
                'created_at':_time(self.created_at).isoformat(),'sequence':self.sequence,
                'usage':self.usage,'context':self.context}
        if self.error: result['error']=self.error
        return result


@dataclass(frozen=True)
class Memory:
    history: str = ''
    through: int = 0
    snapshot_key: str = ''
    snapshot_summary: str = ''
    tail_start: int = 0

    def to_dict(self):
        for name in ('history','snapshot_key','snapshot_summary'): _text(getattr(self,name),name)
        _number(self.through,'through'); _number(self.tail_start,'tail start')
        return {k:v for k,v in asdict(self).items() if v}


COLUMNS='id,ordinal,session_key,generation,client_id,question,answer,status,error,model,snapshot_at,created_at,sequence,usage,context_info'
STATUSES=frozenset(('running','completed','failed','cancelled','interrupted'))


def _model(raw):
    data=_object(raw)
    # Go ignores unknown JSON fields; never forward unknown credential-bearing fields.
    model=Model(**{k:v for k,v in data.items() if k in Model.__dataclass_fields__})
    model.to_dict()
    return model


def _scan(row):
    if len(row)!=15: raise SideInvalid('invalid side request row')
    return Exchange(*row[:9],_model(row[9]),*row[10:13],_object(row[13]),_object(row[14]))


class SideQuestionStore:
    def __init__(self,executor,parent):
        if not isinstance(parent,Parent): raise SideInvalid('expected a Parent')
        self.executor=executor; self.parent=parent; self.key=parent.key()

    def _transaction(self):
        factory=getattr(self.executor,'transaction',None)
        if factory is None: raise TypeError('side writes require a transaction-capable executor')
        return factory()

    def _lock_parent(self,tx):
        p=self.parent
        if p.conversation_id:
            rows=tx.fetch_all(Query('SELECT id FROM conversations WHERE id=$1 FOR SHARE',(p.conversation_id,)))
        else:
            rows=tx.fetch_all(Query('SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL FOR SHARE',(p.task_id,p.exploration_id)))
        if not rows: raise SideParentGone('side parent is deleted or archived')
        if p.intent_id and not tx.fetch_all(Query("SELECT id FROM exploration_nodes WHERE id=$1 AND exploration_id=$2 AND kind='intent' AND state<>'stopped' FOR SHARE",(p.intent_id,p.exploration_id))):
            raise SideParentGone('side worker parent is unavailable')

    def _snapshot(self,s):
        if not isinstance(s,Snapshot) or s.parent!=self.parent: raise SideInvalid('snapshot belongs to another parent')
        return _json(s.to_dict())

    def save_snapshot(self,s):
        raw=self._snapshot(s); p=self.parent
        with self._transaction() as tx:
            self._lock_parent(tx)
            return tx.execute(Query('''INSERT INTO side_question_sessions(session_key,conversation_id,task_id,exploration_id,intent_id,run_id,version,snapshot)
VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(session_key) DO UPDATE SET run_id=EXCLUDED.run_id,version=EXCLUDED.version,snapshot=EXCLUDED.snapshot
WHERE (side_question_sessions.run_id,side_question_sessions.version)<(EXCLUDED.run_id,EXCLUDED.version)''',
                (self.key,p.conversation_id or None,p.task_id or None,p.exploration_id or None,p.intent_id or None,s.run_id,s.version,raw)))==1

    def snapshot(self):
        rows=self.executor.fetch_all(Query('SELECT snapshot FROM side_question_sessions WHERE session_key=$1',(self.key,)))
        if not rows: return None
        data=_object(rows[0][0])
        try:
            s=Snapshot(Parent(**data['parent']),data['run_id'],data['version'],datetime.fromisoformat(data['captured_at'].replace('Z','+00:00')),_model(data['model']),data['request'])
        except (KeyError,TypeError,ValueError): raise SideInvalid('invalid stored snapshot') from None
        self._snapshot(s)
        return s

    def _one(self,sql,params):
        rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM side_question_requests '+sql,params))
        return _scan(rows[0]) if rows else None

    def request(self,id):
        return self._one('WHERE id=$1 AND session_key=$2',(_text(id,'request id'),self.key))

    def existing(self,client_id):
        return self._one('WHERE session_key=$1 AND client_id=$2 AND generation=(SELECT generation FROM side_question_sessions WHERE session_key=$1)',(self.key,_text(client_id,'client id')))

    def current(self): return self._one("WHERE session_key=$1 AND status='running'",(self.key,))

    def history(self,before=0,limit=20):
        _number(before,'before'); _number(limit,'limit',1)
        if limit>100: raise SideInvalid('history limit exceeds 100')
        return [_scan(r) for r in self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM side_question_requests WHERE session_key=$1 AND ($2::bigint=0 OR ordinal<$2) ORDER BY ordinal DESC LIMIT $3',(self.key,before,limit)))]

    def replay(self):
        rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+" FROM side_question_requests WHERE session_key=$1 AND status='completed' ORDER BY ordinal DESC LIMIT 20",(self.key,)))
        return [_scan(r) for r in reversed(rows)]

    def start(self,s,client_id,question):
        self._snapshot(s); _text(client_id,'client id'); _text(question,'question')
        if not client_id or not question: raise SideInvalid('client id and question are required')
        with self._transaction() as tx:
            self._lock_parent(tx)
            rows=tx.fetch_all(Query('SELECT generation FROM side_question_sessions WHERE session_key=$1 FOR UPDATE',(self.key,)))
            if not rows: raise SideSessionMissing('side snapshot has not been persisted')
            generation=rows[0][0]
            rows=tx.fetch_all(Query('SELECT '+COLUMNS+' FROM side_question_requests WHERE session_key=$1 AND generation=$2 AND client_id=$3',(self.key,generation,client_id)))
            if rows:
                e=_scan(rows[0])
                if e.question!=question: raise SideInvalid('request id cannot be reused for another question')
                return e,False
            if tx.fetch_one(Query("SELECT EXISTS(SELECT 1 FROM side_question_requests WHERE session_key=$1 AND status='running')",(self.key,)))[0]:
                raise SideBusy('another side question is already running')
            row=tx.fetch_one(Query("INSERT INTO side_question_requests(id,session_key,generation,client_id,question,status,model,snapshot_at) VALUES($1,$2,$3,$4,$5,'running',$6,$7) RETURNING "+COLUMNS,
                (str(uuid.uuid4()),self.key,generation,client_id,question,_json(s.model.to_dict()),s.captured_at)))
            return _scan(row),True

    def _exchange(self,e):
        if not isinstance(e,Exchange) or e.session_key!=self.key: raise SideInvalid('request belongs to another parent')
        _text(e.id,'request id'); _number(e.generation,'generation'); _number(e.ordinal,'ordinal',1)

    def update(self,e):
        self._exchange(e); _number(e.sequence,'sequence')
        _text(e.answer,'answer'); _text(e.error,'error')
        if not isinstance(e.status,str) or e.status not in STATUSES: raise SideInvalid('invalid request status')
        usage=_json(_object(e.usage)); info=_json(_object(e.context))
        return self.executor.execute(Query('''UPDATE side_question_requests r SET answer=$2,status=$3,error=$4,sequence=$5,usage=$6,context_info=$7
WHERE r.id=$1 AND r.status='running' AND r.sequence<$5 AND r.session_key=$8 AND r.generation=$9
AND EXISTS(SELECT 1 FROM side_question_sessions s WHERE s.session_key=r.session_key AND s.generation=r.generation)''',
            (e.id,e.answer,e.status,e.error,e.sequence,usage,info,self.key,e.generation)))==1

    def clear_history(self):
        """Permanently delete this parent's history; host must authorize deletion."""
        with self._transaction() as tx:
            tx.execute(Query("UPDATE side_question_sessions SET generation=generation+1,memory='{}' WHERE session_key=$1",(self.key,)))
            tx.execute(Query('DELETE FROM side_question_requests WHERE session_key=$1',(self.key,)))

    def memory(self,e):
        self._exchange(e)
        rows=self.executor.fetch_all(Query('''SELECT s.memory FROM side_question_sessions s JOIN side_question_requests r ON r.session_key=s.session_key
WHERE r.id=$1 AND r.generation=s.generation AND s.generation=$2 AND r.status='running' AND s.session_key=$3''',(e.id,e.generation,self.key)))
        if not rows: raise SideParentGone('side request is no longer active')
        data=_object(rows[0][0]); memory=Memory(**{k:v for k,v in data.items() if k in Memory.__dataclass_fields__})
        memory.to_dict()
        return memory

    def replay_page(self,e,after=0):
        self._exchange(e); _number(after,'after')
        return [_scan(r) for r in self.executor.fetch_all(Query('SELECT '+COLUMNS+''' FROM side_question_requests WHERE session_key=$1 AND generation=$2
AND status='completed' AND ordinal>$3 AND ordinal<$4 ORDER BY ordinal LIMIT 20''',(self.key,e.generation,after,e.ordinal)))]

    def save_memory(self,e,memory):
        self._exchange(e)
        if not isinstance(memory,Memory): raise SideInvalid('invalid memory')
        count=self.executor.execute(Query('''UPDATE side_question_sessions s SET memory=$3 WHERE s.session_key=$1 AND s.generation=$2
AND EXISTS(SELECT 1 FROM side_question_requests r WHERE r.id=$4 AND r.session_key=s.session_key AND r.generation=s.generation AND r.status='running')''',
            (self.key,e.generation,_json(memory.to_dict()),e.id)))
        if count!=1: raise SideParentGone('side request is no longer active')

    def interrupt(self):
        """Parent-scoped restart recovery; global source sweep is host-owned."""
        return self.executor.execute(Query("UPDATE side_question_requests SET status='interrupted',error=$2,sequence=sequence+1 WHERE status='running' AND session_key=$1",(self.key,'Service restarted; answer interrupted')))
