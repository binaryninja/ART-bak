"""Conversation metadata only; no agent, transcript or side-question execution.

Source db/conversation.go/schema.sql. Create/profile lock ordering remains child
row then profile FOR KEY SHARE. Agent existence and API-key readiness are HTTP
capabilities, not invented FK guarantees. Delete requires unported coordinated
runtime/side-question/finding-retest/transcript lifecycle, never FK-only success.
"""
from dataclasses import dataclass
from .core import Query,int64
from .configuration_ownership import require_write
from .postgres import NoRowsError
from .transaction_coordination import TransactionCoordinationBinding, coordinated_transaction

def _optional(executor,query):
    try:return executor.fetch_one(query)
    except NoRowsError:return None


class ConversationInvalid(ValueError):pass
class ConversationMissing(LookupError):pass
class ConversationUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class ConversationReadLimits:
    rows:int=1000
    row_bytes:int=65536
    total_bytes:int=1048576
    def validate(self):
        for value,maximum in ((self.rows,10000),(self.row_bytes,1048576),(self.total_bytes,16777216)):
            if type(value)is not int or not 1<=value<=maximum:raise ValueError('Invalid conversation read bounds')
        if self.row_bytes>self.total_bytes:raise ValueError('Conversation row exceeds total bound')

_LIST_ORDER='(pinned_at IS NOT NULL) DESC, pinned_at DESC NULLS LAST, updated_at DESC, id DESC'
_LIST_SQL="""WITH selected AS (
 SELECT id, agent_key, title, llm_profile_id, pinned_at, created_at, updated_at
 FROM conversations ORDER BY """+_LIST_ORDER+""" LIMIT $1
), sized AS (
 SELECT *, octet_length(COALESCE(agent_key,''))::bigint+octet_length(COALESCE(title,''))::bigint AS text_bytes FROM selected
), checked AS (
 SELECT *, COUNT(*) OVER() <= $2 AND MAX(text_bytes) OVER() <= $3
 AND SUM(text_bytes) OVER() <= $4 AS permitted FROM sized
)
SELECT id, CASE WHEN permitted THEN agent_key ELSE NULL END AS agent_key,
 CASE WHEN permitted THEN title ELSE NULL END AS title,
 llm_profile_id, pinned_at, created_at, updated_at
FROM checked ORDER BY """+_LIST_ORDER

COLS='id, agent_key, title, llm_profile_id, pinned_at, created_at, updated_at'

def identifier(value):
    try:int64(value,'conversation ID')
    except ValueError:raise ConversationInvalid('Invalid ID') from None
    if value<=0:raise ConversationInvalid('Invalid ID')
    return value

def text(value,limit,*,empty=False):
    if not isinstance(value,str) or '\x00' in value:raise ConversationInvalid('Invalid text')
    value=value.strip()
    try:value.encode('utf-8')
    except UnicodeError:raise ConversationInvalid('Invalid text') from None
    if len(value)>limit or (not empty and not value):raise ConversationInvalid('Invalid text length')
    return value

@dataclass(frozen=True)
class Conversation:
    id:int
    agent_key:str
    title:str
    llm_profile_id:object
    pinned_at:object
    created_at:object
    updated_at:object
    @property
    def session_id(self):return f'conv-{identifier(self.id)}'
    def to_dict(self):
        def stamp(value):return value.isoformat() if hasattr(value,'isoformat') else value
        result=dict(id=self.id,agent_key=self.agent_key,title=self.title,pinned=self.pinned_at is not None,created_at=stamp(self.created_at),updated_at=stamp(self.updated_at))
        if self.llm_profile_id is not None:result['llm_profile_id']=self.llm_profile_id
        if self.pinned_at is not None:result['pinned_at']=stamp(self.pinned_at)
        return result

def _row(row):
    if row is None:return None
    if len(row)!=7:raise ConversationUnavailable('Invalid conversation row')
    return Conversation(*row)

class ConversationStore:
    def __init__(self,executor,*,read_limits=ConversationReadLimits()):
        if type(read_limits)is not ConversationReadLimits:raise ValueError('Explicit conversation read limits required')
        read_limits.validate()
        self.executor=executor;self.read_limits=read_limits;self._read_limits=read_limits
    def _bounds(self):
        if self.read_limits is not self._read_limits:raise ConversationUnavailable('Conversation bounds changed')
        self.read_limits.validate();return self.read_limits
    def list(self):
        limits=self._bounds()
        rows=self.executor.fetch_all(Query(_LIST_SQL,(limits.rows+1,limits.rows,limits.row_bytes,limits.total_bytes)))
        self._bounds()
        if len(rows)>limits.rows:raise ConversationUnavailable('Conversation list exceeds bounds')
        total=0
        for row in rows:
            if len(row)!=7 or any(type(row[i])is not str for i in (1,2)):raise ConversationUnavailable('Conversation list unavailable')
            size=sum(len(row[i].encode('utf-8')) for i in (1,2));total+=size
            if size>limits.row_bytes or total>limits.total_bytes:raise ConversationUnavailable('Conversation list exceeds bounds')
        return [_row(row) for row in rows]
    def get(self,id):
        return _row(_optional(self.executor,Query('SELECT '+COLS+' FROM conversations WHERE id=$1',(identifier(id),))))
    def _transaction(self):
        if not callable(getattr(self.executor,'transaction',None)):raise ConversationUnavailable('Transaction adapter required')
        return self.executor.transaction()
    def _profile(self,tx,id):
        if id is None:return
        identifier(id)
        if _optional(tx,Query('SELECT id FROM llm_profiles WHERE id=$1 FOR KEY SHARE',(id,))) is None:raise ConversationInvalid('Profile not found')
    def create(self,agent_key,title='',llm_profile_id=None):
        key=text(agent_key,120);title=text(title,200,empty=True) or 'New conversation'
        if llm_profile_id is not None:identifier(llm_profile_id)
        with self._transaction() as tx:
            result=_row(_optional(tx,Query('INSERT INTO conversations(agent_key, title, llm_profile_id) VALUES ($1, $2, NULL)\nRETURNING '+COLS,(key,title))))
            if result is None:raise ConversationUnavailable('Conversation creation failed')
            self._profile(tx,llm_profile_id)
            if llm_profile_id is not None:
                result=_row(_optional(tx,Query('UPDATE conversations SET llm_profile_id=$2\nWHERE id=$1 RETURNING '+COLS,(result.id,llm_profile_id))))
                if result is None:raise ConversationUnavailable('Conversation profile update failed')
            return result
    def patch(self,id,data):
        identifier(id)
        if not isinstance(data,dict) or not data or set(data)-{'title','pinned'}:raise ConversationInvalid('Invalid patch')
        title=text(data['title'],200) if 'title' in data else None
        pinned=data.get('pinned')
        if 'pinned' in data and type(pinned) is not bool:raise ConversationInvalid('Invalid pin')
        return _row(_optional(self.executor,Query('''UPDATE conversations SET
 title = CASE WHEN $2::boolean THEN $3 ELSE title END,
 pinned_at = CASE WHEN $4::boolean IS NULL THEN pinned_at
 WHEN $4::boolean THEN COALESCE(pinned_at, now()) ELSE NULL END
WHERE id=$1 RETURNING '''+COLS,(id,'title' in data,title,pinned))))
    def update_profile(self, id, profile_id, *, transaction_coordination=None):
        identifier(id)
        if profile_id is not None:identifier(profile_id)
        operation = 'conversations.update_profile'
        executor = self.executor
        require_write(executor, operation)
        if transaction_coordination is None:
            transaction = self._transaction()
        else:
            if (type(transaction_coordination) is not TransactionCoordinationBinding
                    or transaction_coordination.executor is not executor):
                raise ConversationInvalid('Coordination binding must match the conversation executor')
            transaction = coordinated_transaction(transaction_coordination, operation=operation, mode='write')
        with transaction as tx:
            if transaction_coordination is not None and self.executor is not executor:
                raise ConversationInvalid('Conversation executor changed during coordination')
            require_write(self.executor, operation)
            if _optional(tx,Query('SELECT id FROM conversations WHERE id=$1 FOR UPDATE',(id,))) is None:raise ConversationMissing()
            self._profile(tx,profile_id)
            tx.execute(Query('UPDATE conversations SET llm_profile_id=$2 WHERE id=$1',(id,profile_id)))
    def delete(self,*args,**kwargs):raise ConversationUnavailable('Coordinated conversation deletion is not ported')
