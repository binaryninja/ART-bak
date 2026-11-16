"""Read-only bounded channel metadata with deliberately stricter redaction."""
import json,math,re
from dataclasses import asdict
from urllib.parse import urlsplit
from .core import Query
from .intercept_detail_read import _time
from ..notify import CHANNELS,parse_filter,MASKED_PREFIX

class ChannelReadUnavailable(RuntimeError):pass
# Inspected cfg* consumers in notify/{email,telegram,webhook}.go. Template is
# deliberately omitted: arbitrary template literals can contain credentials.
ALLOWED={'email':('host','port','tls','from','to','username'),
         'telegram':('chat_id','base_url'),'webhook':('method',),
         'dingtalk':(),'feishu':(),'wecom':()}

def validate_json(value,depth=0,counter=None):
    if counter is None:counter=[0]
    counter[0]+=1
    if depth>32 or counter[0]>10000:raise ChannelReadUnavailable()
    if value is None or type(value) in (bool,int):return
    if type(value) is float and math.isfinite(value):return
    if type(value) is str:value.encode('utf-8');return
    if type(value) is list:
        for item in value:validate_json(item,depth+1,counter)
        return
    if type(value) is dict:
        for key,item in value.items():
            if type(key) is not str:raise ChannelReadUnavailable()
            validate_json(key,depth+1,counter);validate_json(item,depth+1,counter)
        return
    raise ChannelReadUnavailable()

def redacted_config(kind,config):
    if kind not in CHANNELS or type(config) is not dict:return {}
    out={key:MASKED_PREFIX for key in CHANNELS[kind].secret_keys if key in config}
    for key in ALLOWED[kind]:
        if key not in config:continue
        value=config[key];safe=False
        if key=='port':safe=type(value) is int and 0<value<=65535
        elif key=='tls':safe=type(value) is bool
        elif key=='method':safe=type(value) is str and value in ('GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS')
        elif key=='base_url' and type(value) is str:
            try:
                u=urlsplit(value)
                port=u.port  # Validate numeric/range syntax even though no connection is made.
                safe=(value=='' or (u.scheme in ('http','https') and bool(u.hostname) and u.username is None and u.password is None and not u.query and not u.fragment and u.path in ('','/') and not any(ord(c)<33 or 127<=ord(c)<=159 for c in value)))
            except ValueError:pass
        elif key=='host':safe=type(value) is str and bool(re.fullmatch(r'[A-Za-z0-9.\-:\[\]]{1,253}',value))
        elif key=='to':safe=type(value) is list and all(type(v) is str and len(v.encode())<=4096 and not any(ord(c)<32 for c in v) and '://' not in v and '?' not in v and '#' not in v for v in value)
        else:safe=type(value) is str and len(value.encode())<=4096 and not any(ord(c)<32 for c in value) and '://' not in value and '?' not in value and '#' not in value
        out[key]=value if safe else MASKED_PREFIX
    return out

def filter_dto(value):
    result=asdict(parse_filter('{}'))
    for key in ('task_ids','asset_ids','vulnclass_include','vulnclass_exclude'):result[key]=None
    if value is None:return result
    if type(value) is not dict:raise ChannelReadUnavailable()
    seen=set()
    for name,item in value.items():
        key=next((k for k in result if k.lower()==name.lower()),None)
        if key is None:continue
        if key in seen:raise ChannelReadUnavailable()
        seen.add(key)
        if item is None:continue
        if key=='min_severity':valid=type(item) is str
        elif key=='on_status_change':valid=type(item) is bool
        else:
            typ=int if key in ('task_ids','asset_ids') else str
            valid=type(item) is list and all(type(x) is typ and (typ is not int or -(2**63)<=x<2**63) for x in item)
        if not valid:raise ChannelReadUnavailable()
        result[key]=item
    return result

class NotificationChannelsReader:
    def __init__(self,executor,*,max_rows=500,max_bytes=1048576):
        if not callable(getattr(executor,'fetch_all',None)) or type(max_rows) is not int or not 1<=max_rows<=2000 or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Explicit bounded channel reader required')
        self.executor=executor;self.max_rows=max_rows;self.max_bytes=max_bytes
        self._pins=(executor,max_rows,max_bytes,tuple(CHANNELS.items()),tuple(ALLOWED.items()))
    def validate(self):
        e,r,b,c,a=self._pins
        if self.executor is not e or type(self.max_rows) is not int or self.max_rows!=r or type(self.max_bytes) is not int or self.max_bytes!=b or tuple(CHANNELS.items())!=c or tuple(ALLOWED.items())!=a:raise ChannelReadUnavailable()
    def read(self):
        self.validate()
        sql="""WITH selected AS (SELECT id,name,kind,enabled,config,mode,filter,rate_per_min,created_at,updated_at FROM notification_channels ORDER BY enabled DESC,id LIMIT $1), bounded AS (SELECT *,SUM(octet_length(name)+octet_length(kind)+octet_length(mode)+octet_length(config::text)+octet_length(filter::text)) OVER () AS bytes FROM selected) SELECT bytes<=$2,id,CASE WHEN bytes<=$2 THEN name ELSE NULL END,CASE WHEN bytes<=$2 THEN kind ELSE NULL END,enabled,CASE WHEN bytes<=$2 THEN config::text ELSE NULL END,CASE WHEN bytes<=$2 THEN mode ELSE NULL END,CASE WHEN bytes<=$2 THEN filter::text ELSE NULL END,rate_per_min,created_at,updated_at FROM bounded ORDER BY enabled DESC,id"""
        rows=self.executor.fetch_all(Query(sql,(self.max_rows+1,self.max_bytes)));self.validate()
        if type(rows) not in (list,tuple) or len(rows)>self.max_rows:raise ChannelReadUnavailable()
        out=[];used=0;seen=set();last=None
        for row in rows:
            if type(row) not in (tuple,list) or len(row)!=11 or row[0] is not True:raise ChannelReadUnavailable()
            _,id,name,kind,enabled,config,mode,filter_,rate,created,updated=row
            if type(id) is not int or not 0<id<2**63 or id in seen or type(enabled) is not bool or type(rate) is not int or not -(2**63)<=rate<2**63:raise ChannelReadUnavailable()
            seen.add(id);order=(not enabled,id)
            if last is not None and order<last:raise ChannelReadUnavailable()
            last=order
            for text in (name,kind,config,mode,filter_):
                if type(text) is not str:raise ChannelReadUnavailable()
                used+=len(text.encode('utf-8'))
            if used>self.max_bytes:raise ChannelReadUnavailable()
            cfg=json.loads(config);f=json.loads(filter_);validate_json(cfg);validate_json(f)
            projected=redacted_config(kind,cfg)
            out.append(dict(id=id,name=name,kind=kind,enabled=enabled,mode=mode,config=projected,filter=filter_dto(f),rate_per_min=rate,created_at=_time(created),updated_at=_time(updated),secret_keys=list(CHANNELS[kind].secret_keys) if kind in CHANNELS else []))
        return {'channels':out}
