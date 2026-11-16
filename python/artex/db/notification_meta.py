"""Bounded notification overview data, never notification delivery authority."""
from .core import Query
from ..notify import CHANNELS,kinds

KEYS=('notify_enabled','notify_public_base_url','notify_digest_interval_min')
STATS=('channels','channels_on','pending','failed','sent_today','backlog_age_ms')
class NotificationMetaUnavailable(RuntimeError):pass

class NotificationMetaReader:
    def __init__(self,executor,*,max_bytes=65536):
        if not callable(getattr(executor,'fetch_all',None)) or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Explicit storage and finite setting bounds required')
        self.executor=executor;self.max_bytes=max_bytes;self._pins=(executor,max_bytes)
        self._kinds=tuple((k,CHANNELS[k].default_rate_per_min,tuple(CHANNELS[k].secret_keys)) for k in kinds())
    def validate(self):
        if self.executor is not self._pins[0] or type(self.max_bytes) is not int or self.max_bytes!=self._pins[1]:raise NotificationMetaUnavailable()
        if tuple((k,CHANNELS[k].default_rate_per_min,tuple(CHANNELS[k].secret_keys)) for k in kinds())!=self._kinds:raise NotificationMetaUnavailable()
    def read(self):
        self.validate()
        rows=self.executor.fetch_all(Query('WITH selected AS (SELECT key,value FROM settings WHERE key=ANY($1::text[])), bounded AS (SELECT *,SUM(octet_length(value)) OVER () AS bytes FROM selected) SELECT key,CASE WHEN bytes<=$2 THEN value ELSE NULL END FROM bounded ORDER BY key',(list(KEYS),self.max_bytes)))
        self.validate();values={};used=0
        if type(rows) not in (list,tuple) or len(rows)>3:raise NotificationMetaUnavailable()
        for row in rows:
            if type(row) not in (list,tuple) or len(row)!=2:raise NotificationMetaUnavailable()
            key,value=row
            if type(key) is not str or key not in KEYS or key in values or type(value) is not str:raise NotificationMetaUnavailable()
            used+=len(value.encode('utf-8'));values[key]=value
        if used>self.max_bytes:raise NotificationMetaUnavailable()
        rows=self.executor.fetch_all(Query("""SELECT
(SELECT count(*) FROM notification_channels),
(SELECT count(*) FROM notification_channels WHERE enabled),
(SELECT count(*) FROM notification_deliveries WHERE state IN ($1,$2)),
(SELECT count(*) FROM notification_deliveries WHERE state=$3),
(SELECT count(*) FROM notification_deliveries WHERE state=$4 AND sent_at >= date_trunc('day', now())),
COALESCE((SELECT EXTRACT(EPOCH FROM (now() - min(created_at))) * 1000 FROM notification_deliveries WHERE state=$1),0)::bigint""",('pending','sending','failed','sent')))
        self.validate()
        if type(rows) not in (list,tuple) or len(rows)!=1 or type(rows[0]) not in (list,tuple) or len(rows[0])!=6:raise NotificationMetaUnavailable()
        for i,value in enumerate(rows[0]):
            if type(value) is not int or not -(2**63)<=value<2**63 or (i<5 and value<0):raise NotificationMetaUnavailable()
        enabled=values.get('notify_enabled')
        return dict(kinds=[dict(kind=k,default_rate_per_min=rate,secret_keys=list(keys)) for k,rate,keys in self._kinds],enabled=True if enabled is None else enabled in ('true','1'),public_base_url=values.get('notify_public_base_url',''),digest_interval_min=values.get('notify_digest_interval_min',''),defaults={'digest_interval_min':30},stats=dict(zip(STATS,rows[0])))
