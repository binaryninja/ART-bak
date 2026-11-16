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

@dataclass(frozen=True)
class ActivityReadLimits:
    row_bytes:int=65536
    page_bytes:int=1048576
    detail_bytes:int=1048576
    def validate(self):
        for value,maximum in ((self.row_bytes,1048576),(self.page_bytes,16777216),(self.detail_bytes,16777216)):
            if type(value)is not int or not 1<=value<=maximum:raise ValueError('Invalid activity read byte bounds')
        if self.row_bytes>self.page_bytes:raise ValueError('Activity row bound exceeds page bound')

_TEXT_FIELDS=('worker','kind','tool','tool_use_id','summary')
_NAMES=('id','worker','kind','tool','tool_use_id','is_error','summary','created_at','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')
_PROJECT=', '.join("COALESCE("+n+",'') AS "+n if n in _TEXT_FIELDS else n for n in _NAMES)
_MASKED=', '.join('CASE WHEN permitted THEN '+n+' ELSE NULL END AS '+n if n in _TEXT_FIELDS else n for n in _NAMES)
_ROW_BYTES='+'.join('octet_length('+n+')::bigint' for n in _TEXT_FIELDS)

def _read_sql(where,descending=False):
    order='DESC' if descending else 'ASC'
    return ('WITH selected AS (SELECT '+_PROJECT+' FROM conversation_activities WHERE '+where+
            ' ORDER BY id '+order+' LIMIT $3), sized AS (SELECT *, '+_ROW_BYTES+' AS row_bytes FROM selected), '+
            'checked AS (SELECT *, MAX(row_bytes) OVER() <= $4 AND SUM(row_bytes) OVER() <= $5 AS permitted FROM sized) '+
            'SELECT '+_MASKED+' FROM checked ORDER BY id '+order)


def _bounded_rows(rows,limits,maximum):
    if len(rows)>maximum:raise ActivityUnavailable('Activity read exceeds bound')
    total=0
    for row in rows:
        if len(row)!=12:raise ActivityUnavailable('Activity read unavailable')
        values=[row[i] for i in (1,2,3,4,6)]
        if any(type(v)is not str for v in values):raise ActivityUnavailable('Activity read exceeds bound')
        size=sum(len(v.encode('utf-8')) for v in values);total+=size
        if size>limits.row_bytes or total>limits.page_bytes:raise ActivityUnavailable('Activity read exceeds bound')



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
    def __init__(self,executor,conversation_id,*,read_limits=ActivityReadLimits()):
        if type(read_limits)is not ActivityReadLimits:raise ValueError('Explicit activity read bounds required')
        read_limits.validate()
        self.executor=executor;self.conversation_id=_id(conversation_id);self.read_limits=read_limits;self._read_limits=read_limits
    def _read_bounds(self):
        if self.read_limits is not self._read_limits:raise ActivityUnavailable('Activity read bounds changed')
        self._read_limits.validate();return self._read_limits

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
        since_id=_id(since_id,'since id',False);limit=_limit(limit,500);bounds=self._read_bounds()
        rows=self.executor.fetch_all(Query(_read_sql('conversation_id=$1 AND id>$2'),
                                           (self.conversation_id,since_id,limit,bounds.row_bytes,bounds.page_bytes)))
        self._read_bounds();_bounded_rows(rows,bounds,limit)
        items=[_scan(row) for row in rows]
        return items,max([since_id]+[item.id for item in items])

    def page(self,before=0,limit=200):
        before=_id(before,'before id',False);limit=_limit(limit,200);bounds=self._read_bounds()
        rows=self.executor.fetch_all(Query(_read_sql('conversation_id=$1 AND ($2 <= 0 OR id < $2)',True),
                                           (self.conversation_id,before,limit+1,bounds.row_bytes,bounds.page_bytes)))
        self._read_bounds();_bounded_rows(rows,bounds,limit+1)
        has_more=len(rows)>limit
        return [_scan(row) for row in reversed(rows[:limit])],has_more

    def detail(self,id):
        id=_id(id,'activity id');bounds=self._read_bounds()
        rows=self.executor.fetch_all(Query("SELECT CASE WHEN octet_length(COALESCE(detail,'')) <= $3 THEN COALESCE(detail,'') ELSE NULL END FROM conversation_activities WHERE id=$1 AND conversation_id=$2",(id,self.conversation_id,bounds.detail_bytes)))
        self._read_bounds()
        if not rows:return ''
        if len(rows)!=1 or len(rows[0])!=1 or type(rows[0][0])is not str or len(rows[0][0].encode('utf-8'))>bounds.detail_bytes:
            raise ActivityUnavailable('Activity detail exceeds bound')
        return rows[0][0]
