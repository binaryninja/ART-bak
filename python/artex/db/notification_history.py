"""Bounded delivery history read only: never claims, retries or sends deliveries."""
from dataclasses import dataclass
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import json,re
from .core import Query
class HistoryUnavailable(RuntimeError):pass
@dataclass(frozen=True)
class DeliverySelection:
    channel_id:int=0
    state:str=''
    event_kind:str=''
    page:int=1
    page_size:int=50
    def validate(self):
        for value in (self.channel_id,self.page,self.page_size):
            if type(value) is not int or not -2**63<=value<2**63:raise ValueError('Integer selection required')
        for value in (self.state,self.event_kind):
            if type(value) is not str or len(value.encode())>256 or '\0' in value:raise ValueError('Bounded filter required')
        if self.page<1 or self.page_size<1:raise ValueError('Positive pagination required')
        if (self.page-1)*(self.page_size if self.page_size<=200 else 50)>1000000:raise ValueError('Page exceeds bound')

def timestamp(value):
    if type(value) is not datetime or type(value.tzinfo) not in (timezone,ZoneInfo):raise HistoryUnavailable()
    text=value.isoformat()
    if len(text)>40 or value.utcoffset() is None:raise HistoryUnavailable()
    return re.sub(r'(\.\d*?[1-9])0+(?=[+-]|$)',r'\1',text).replace('+00:00','Z')

class NotificationHistoryReader:
    def __init__(self,executor,*,max_bytes=1048576):
        if not callable(getattr(executor,'fetch_all',None)) or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Bounded executor required')
        self.executor=executor;self.max_bytes=max_bytes;self._pins=(executor,max_bytes)
    def validate(self):
        if self.executor is not self._pins[0] or self.max_bytes!=self._pins[1]:raise HistoryUnavailable()
    def read(self,selection):
        if type(selection) is not DeliverySelection:raise ValueError('Exact selection required')
        selection.validate();self.validate();size=selection.page_size if selection.page_size<=200 else 50
        # Count intentionally does not join channels, matching source. Page does.
        fields=('id','finding_id','event_kind','channel_id','channel_name','channel_kind','state','attempts','last_error','batch_id','created_at','sent_at','next_attempt_at','snapshot')
        texts=('event_kind','channel_name','channel_kind','state','last_error','snapshot')
        sql="""WITH filtered AS (SELECT d.id,e.finding_id,e.kind AS event_kind,d.channel_id,d.state,d.attempts,
COALESCE(d.last_error,'') AS last_error,d.batch_id,d.created_at,d.sent_at,d.next_attempt_at,e.snapshot::text AS snapshot
FROM notification_deliveries d JOIN notification_events e ON e.id=d.event_id
WHERE ($1<=0 OR d.channel_id=$1) AND ($2='' OR d.state=$2) AND ($3='' OR e.kind=$3)),
page AS (SELECT f.*,c.name AS channel_name,c.kind AS channel_kind FROM filtered f
JOIN notification_channels c ON c.id=f.channel_id ORDER BY f.id DESC LIMIT $4 OFFSET $5),
bounded AS (SELECT *,SUM("""+'+'.join('COALESCE(octet_length('+f+'),0)' for f in texts)+""") OVER () AS bytes FROM page)
SELECT (SELECT count(*) FROM filtered),"""+','.join('CASE WHEN b.bytes<=$6 THEN b.'+f+' ELSE NULL END' if f in texts else 'b.'+f for f in fields)+" FROM (SELECT 1) sentinel LEFT JOIN bounded b ON true ORDER BY b.id DESC"
        rows=self._pins[0].fetch_all(Query(sql,(selection.channel_id,selection.state,selection.event_kind,size,(selection.page-1)*size,self.max_bytes)));self.validate()
        if type(rows) not in (list,tuple) or not rows or len(rows)>size:raise HistoryUnavailable()
        total=None;out=[];last=2**63;used=0
        for row in rows:
            if type(row) not in (list,tuple) or len(row)!=15:raise HistoryUnavailable()
            count=row[0]
            if type(count) is not int or not 0<=count<2**63 or total is not None and total!=count:raise HistoryUnavailable()
            total=count
            if row[1] is None:
                if len(rows)!=1 or any(v is not None for v in row[1:]):raise HistoryUnavailable()
                continue
            d=dict(zip(fields,row[1:]))
            for key in texts:
                if type(d[key]) is not str:raise HistoryUnavailable()
                used+=len(d[key].encode())
            if used>self.max_bytes:raise HistoryUnavailable()
            for key in ('id','finding_id','channel_id','attempts'):
                if type(d[key]) is not int or not 0<=d[key]<2**63:raise HistoryUnavailable()
            if d['id']<=0 or d['id']>=last:raise HistoryUnavailable()
            last=d['id']
            if selection.channel_id>0 and d['channel_id']!=selection.channel_id or selection.state and d['state']!=selection.state or selection.event_kind and d['event_kind']!=selection.event_kind:raise HistoryUnavailable()
            if d['batch_id'] is None:del d['batch_id']
            elif type(d['batch_id']) is not int or not -2**63<=d['batch_id']<2**63:raise HistoryUnavailable()
            for key in ('created_at','next_attempt_at'):d[key]=timestamp(d[key])
            if d['sent_at'] is None:del d['sent_at']
            else:d['sent_at']=timestamp(d['sent_at'])
            try:snapshot=json.loads(d.pop('snapshot'))
            except (ValueError,RecursionError):raise HistoryUnavailable() from None
            # JSONB guarantees valid JSON; Go's typed string fields stay empty on
            # null/wrong types and decoding continues for other string fields.
            values={'name':'','vulnclass':'','severity':''}
            if type(snapshot) is dict:
                for key,value in snapshot.items():
                    folded=key.lower()
                    if folded in values and type(value) is str:values[folded]=value
            d['title']=values['name'] or values['vulnclass'];d['severity']=values['severity'];d['finding_id']=str(d['finding_id']);out.append(d)
        return {'deliveries':out,'total':total,'page':selection.page,'page_size':selection.page_size}
