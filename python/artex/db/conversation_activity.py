"""Conversation activity persistence from db/conversation.go, not JSONL history.

Parent-bound queries preserve source append/list/reverse-page/detail semantics.
Append uses the real conversation foreign key (ON DELETE CASCADE); missing-parent
constraint errors propagate, never silently insert unowned rows. Missing-parent
reads return empty results as upstream. Summary queries never select detail.

Activity includes the exact shared db/exploration.go DTO fields; node_id, metadata,
source_task_id, inherited and main_seg belong to exploration activities and are
NOT persisted by AppendConvActivity upstream or here. Conversation reads leave
those fields at their omitted defaults. Token fields are nullable INTEGERs;
zero is distinct from absent. The caller supplies usage only on terminal result
rows, as upstream; this store does not invent usage/transcript projections.

Nonpositive limits use source defaults (500 list, 200 page). Explicit limits above
10000 are rejected as a deliberate bound; signed cursors retain source semantics.
Text removes NULs and replaces consecutive invalid UTF-8 bytes with U+FFFD as Go
utf8Clean does. Python surrogate-containing strings are encoded with surrogatepass
before that cleanup; byte inputs support captured non-UTF-8 activity payloads.
No parent lifecycle, dispatcher, HTTP route, or transcript wiring is automatic.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime,timezone
import re
from .core import Query,int64
from .postgres import NoRowsError


class ActivityInvalid(ValueError):pass
class ActivityMissing(LookupError):pass
class ActivityUnavailable(RuntimeError):pass


def _id(value,name='conversation id',positive=True):
    try:value=int64(value,name)
    except ValueError:raise ActivityInvalid(f'invalid {name}') from None
    if positive and value<=0:raise ActivityInvalid(f'invalid {name}')
    return value


def _limit(value,default):
    if type(value) is not int:raise ActivityInvalid('limit must be an integer')
    if value<=0:return default
    if value>10000:raise ActivityInvalid('activity limit exceeds 10000')
    return value


def _text(value):
    if isinstance(value,str):value=value.encode('utf-8','surrogatepass')
    if not isinstance(value,bytes):raise ActivityInvalid('activity text must be text or bytes')
    decoded=value.replace(b'\0',b'').decode('utf-8','surrogateescape')
    return re.sub(r'[\udc80-\udcff]+','\ufffd',decoded)


def _tokens(value):
    if value is not None and (type(value) is not int or not -(2**31)<=value<2**31):
        raise ActivityInvalid('activity token count must fit nullable INTEGER')
    return value


@dataclass
class Activity:
    id: int = 0
    node_id: int | None = None
    worker: str = ''
    kind: str = ''
    tool: str = ''
    tool_use_id: str = ''
    is_error: bool = False
    summary: str = ''
    detail: str = ''
    metadata: object | None = None
    created_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    source_task_id: int = 0
    inherited: bool = False
    main_seg: int | None = None

    def to_dict(self):
        result={'id':self.id,'is_error':self.is_error,'created_at':self.created_at.isoformat()}
        for name in ('worker','kind','tool','tool_use_id','summary'):
            value=getattr(self,name)
            if value:result[name]=_text(value)
        for name in ('node_id','metadata','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','main_seg'):
            value=getattr(self,name)
            if value is not None:result[name]=deepcopy(value)
        if self.source_task_id:result['source_task_id']=self.source_task_id
        if self.inherited:result['inherited']=True
        return result


COLUMNS="id, COALESCE(worker,''), COALESCE(kind,''), COALESCE(tool,''), COALESCE(tool_use_id,''), is_error, COALESCE(summary,''), created_at, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens"


def _scan(row):
    if len(row)!=12:raise ActivityInvalid('invalid activity row shape')
    return Activity(id=row[0],worker=row[1],kind=row[2],tool=row[3],tool_use_id=row[4],is_error=row[5],summary=row[6],created_at=row[7],
                    input_tokens=row[8],output_tokens=row[9],cache_read_tokens=row[10],cache_write_tokens=row[11])


class ConversationActivityStore:
    def __init__(self,executor,conversation_id):
        self.executor=executor;self.conversation_id=_id(conversation_id)

    def append(self,activity):
        if not isinstance(activity,Activity):raise ActivityInvalid('Activity required')
        if type(activity.is_error) is not bool:raise ActivityInvalid('is_error must be boolean')
        fields=[_text(getattr(activity,name)) for name in ('worker','kind','tool','tool_use_id')]
        fields+=[activity.is_error,_text(activity.summary),_text(activity.detail)]
        fields+=[_tokens(getattr(activity,name)) for name in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')]
        row=self.executor.fetch_one(Query('''INSERT INTO conversation_activities(conversation_id, worker, kind, tool, tool_use_id, is_error, summary, detail, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
VALUES ($1,NULLIF($2,''),NULLIF($3,''),NULLIF($4,''),NULLIF($5,''),$6,NULLIF($7,''),NULLIF($8,''),$9,$10,$11,$12)
RETURNING id''',(self.conversation_id,*fields)))
        return row[0]

    def append_many_and_touch(self,items,title_if_default=None):
        """Atomically project a batch and bump conversation metadata.

        This transaction covers SQL only, never a JSONL transcript commit. Caller
        must report/reconcile cross-store projection failure rather than rerun a
        tool or claim distributed atomicity. No automatic retry/idempotency here.
        The optional already-derived title replaces only the exact English
        default, never a user title. Empty batches still touch an existing parent.
        """
        if not isinstance(items,(list,tuple)) or len(items)>10000:
            raise ActivityInvalid('bounded activity sequence required')
        if title_if_default is not None:
            from .conversations import text
            title_if_default=text(title_if_default,200)
        # Validate before acquiring a connection, including every item in a batch.
        for item in items:
            if not isinstance(item,Activity) or type(item.is_error) is not bool:
                raise ActivityInvalid('valid Activity required')
            for name in ('worker','kind','tool','tool_use_id','summary','detail'):
                _text(getattr(item,name))
            for name in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'):
                _tokens(getattr(item,name))
        transaction=getattr(self.executor,'transaction',None)
        if not callable(transaction):raise ActivityUnavailable('Transaction adapter required')
        with transaction() as tx:
            try:row=tx.fetch_one(Query('SELECT id FROM conversations WHERE id=$1 FOR UPDATE',(self.conversation_id,)))
            except NoRowsError:row=None
            if row is None:raise ActivityMissing('Conversation not found')
            scoped=ConversationActivityStore(tx,self.conversation_id)
            ids=[scoped.append(item) for item in items]
            changed=tx.execute(Query("""UPDATE conversations SET updated_at=now(),
 title=CASE WHEN title='New conversation' AND $2::text IS NOT NULL THEN $2 ELSE title END
WHERE id=$1""",(self.conversation_id,title_if_default)))
            if changed!=1:raise ActivityUnavailable('Conversation metadata update failed')
            return ids

    def list(self,since_id=0,limit=500):
        since_id=_id(since_id,'since id',False);limit=_limit(limit,500)
        rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM conversation_activities WHERE conversation_id=$1 AND id>$2 ORDER BY id LIMIT $3',
                                           (self.conversation_id,since_id,limit)))
        items=[_scan(row) for row in rows]
        return items,max([since_id]+[item.id for item in items])

    def page(self,before=0,limit=200):
        before=_id(before,'before id',False);limit=_limit(limit,200)
        rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+''' FROM conversation_activities
WHERE conversation_id=$1 AND ($2 <= 0 OR id < $2)
ORDER BY id DESC LIMIT $3''',(self.conversation_id,before,limit+1)))
        has_more=len(rows)>limit
        return [_scan(row) for row in reversed(rows[:limit])],has_more

    def detail(self,id):
        id=_id(id,'activity id')
        rows=self.executor.fetch_all(Query('SELECT detail FROM conversation_activities WHERE id=$1 AND conversation_id=$2',(id,self.conversation_id)))
        return rows[0][0] or '' if rows else ''
