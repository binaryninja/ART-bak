"""Conversation metadata only; no agent, transcript or side-question execution.

Source db/conversation.go/schema.sql. Create/profile lock ordering remains child
row then profile FOR KEY SHARE. Agent existence and API-key readiness are HTTP
capabilities, not invented FK guarantees. Delete requires unported coordinated
runtime/side-question/finding-retest/transcript lifecycle, never FK-only success.
"""
from dataclasses import dataclass
from .core import Query,int64
from .postgres import NoRowsError

def _optional(executor,query):
    try:return executor.fetch_one(query)
    except NoRowsError:return None


class ConversationInvalid(ValueError):pass
class ConversationMissing(LookupError):pass
class ConversationUnavailable(RuntimeError):pass

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
    def __init__(self,executor):self.executor=executor
    def list(self):
        return [_row(row) for row in self.executor.fetch_all(Query('SELECT '+COLS+' FROM conversations\nORDER BY (pinned_at IS NOT NULL) DESC, pinned_at DESC NULLS LAST, updated_at DESC, id DESC',()))]
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
    def update_profile(self,id,profile_id):
        identifier(id)
        if profile_id is not None:identifier(profile_id)
        with self._transaction() as tx:
            if _optional(tx,Query('SELECT id FROM conversations WHERE id=$1 FOR UPDATE',(id,))) is None:raise ConversationMissing()
            self._profile(tx,profile_id)
            tx.execute(Query('UPDATE conversations SET llm_profile_id=$2 WHERE id=$1',(id,profile_id)))
    def delete(self,*args,**kwargs):raise ConversationUnavailable('Coordinated conversation deletion is not ported')
