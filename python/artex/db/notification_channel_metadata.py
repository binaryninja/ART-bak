"""Metadata-only channel PATCH; never reads or assigns configuration/credentials."""
from dataclasses import dataclass
import json
from .core import Query
from .judge_config_read import SPACE
from .notification_channels_read import filter_dto,validate_json

class MetadataDenied(PermissionError):pass
class MetadataMissing(LookupError):pass
class MetadataUnavailable(RuntimeError):pass
class MetadataUncertain(RuntimeError):pass
@dataclass(frozen=True)
class ChannelMetadataScope:
    name:str
    revision:str
@dataclass(frozen=True)
class ChannelMetadataPatch:
    channel_id:int
    changes_json:str

def proposal(id,values):
    if type(id) is not int or not 0<id<2**63 or type(values) is not dict:raise ValueError('Invalid channel metadata')
    if set(values)-{'name','enabled','mode','rate_per_min','filter'}:raise ValueError('Only metadata fields supported; config and kind are not editable')
    validate_json(values);out={}
    for key,value in values.items():
        if value is None:continue  # Source pointer-null means omitted.
        if key=='name':
            if type(value) is not str:raise ValueError('Invalid name')
            value=value.strip(SPACE)
            if not value or len(value.encode())>4096:raise ValueError('Bounded nonempty name required')
        elif key=='enabled':
            if type(value) is not bool:raise ValueError('Boolean enabled required')
        elif key=='mode':
            if type(value) is not str or value not in ('realtime','digest'):raise ValueError('Invalid notification mode')
        elif key=='rate_per_min':
            if type(value) is not int or not 0<=value<2**63:raise ValueError('Nonnegative rate required')
        elif key=='filter':
            if type(value) is not dict:raise ValueError('Filter object required')
            value=filter_dto(value)
            if value['min_severity'] not in ('','low','medium','high','critical'):raise ValueError('Invalid minimum severity')
        out[key]=value
    text=json.dumps(out,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if len(text.encode())>65536:raise ValueError('Metadata patch exceeds bound')
    return ChannelMetadataPatch(id,text)

class ChannelMetadataStore:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard):
        if type(scope) is not ChannelMetadataScope or any(type(x) is not str or not x or len(x.encode())>256 for x in (scope.name,scope.revision)):raise ValueError('Explicit metadata scope required')
        if not callable(getattr(executor,'transaction',None)) or any(not callable(x) for x in (authorize,current,lifecycle_guard)):raise ValueError('Explicit transactional storage and write authority required')
        self.executor=executor;self.scope=scope;self.authorize=authorize;self.current=current;self.guard=lifecycle_guard
        self._pins=(executor,scope,scope.name,scope.revision,authorize,current,lifecycle_guard)
    def validate(self):
        e,s,n,r,a,c,g=self._pins
        if self.executor is not e or self.scope is not s or (s.name,s.revision)!=(n,r) or self.authorize is not a or self.current is not c or self.guard is not g:raise MetadataDenied()
    def patch(self,p,*,request_authorize):
        if type(p) is not ChannelMetadataPatch or type(p.changes_json) is not str or not callable(request_authorize):raise ValueError('Exact immutable patch required')
        if proposal(p.channel_id,json.loads(p.changes_json))!=p:raise ValueError('Canonical normalized patch required')
        original=(p.channel_id,p.changes_json);values=json.loads(p.changes_json);attempted=False
        e,s,n,r,a,c,g=self._pins
        def check():
            self.validate()
            if (p.channel_id,p.changes_json)!=original or type(p.channel_id) is not int or type(p.changes_json) is not str:raise MetadataDenied()
        def allow():
            check()
            if c(s) is not True:raise MetadataDenied()
            check()
            if a(s,p) is not True:raise MetadataDenied()
            check()
            if request_authorize(s,p) is not True:raise MetadataDenied()
            check()
            if a(s,p) is not True:raise MetadataDenied()
            check()
            if c(s) is not True:raise MetadataDenied()
            check()
        try:
            allow()
            with g(s):
                allow()
                with e.transaction() as tx:
                    rows=tx.fetch_all(Query('SELECT id,enabled FROM notification_channels WHERE id=$1 FOR UPDATE',(p.channel_id,)))
                    allow()
                    if not rows:raise MetadataMissing()
                    if type(rows) not in (list,tuple) or len(rows)!=1 or type(rows[0]) not in (list,tuple) or len(rows[0])!=2 or type(rows[0][0]) is not int or rows[0][0]!=p.channel_id or type(rows[0][1]) is not bool:raise MetadataUnavailable()
                    previous=rows[0][1]
                    if values:
                        columns=[];params=[p.channel_id]
                        for key in ('name','enabled','mode','rate_per_min','filter'):
                            if key not in values:continue
                            params.append(json.dumps(values[key],ensure_ascii=False,separators=(',',':')) if key=='filter' else values[key])
                            columns.append(key+'=$'+str(len(params))+('::jsonb' if key=='filter' else ''))
                        allow();attempted=True
                        count=tx.execute(Query('UPDATE notification_channels SET '+','.join(columns)+' WHERE id=$1',tuple(params)))
                        if type(count) is not int or count!=1:raise MetadataUncertain()
                        if previous and values.get('enabled') is False:
                            allow()
                            count=tx.execute(Query('UPDATE notification_deliveries SET state=$2,last_error=$3 WHERE channel_id=$1 AND state IN ($4,$5)',(p.channel_id,'skipped','Channel disabled','pending','sending')))
                            if type(count) is not int or count<0:raise MetadataUncertain()
                    allow() # Original write authority immediately before transaction exit.
                allow()
            allow();return p.channel_id
        except BaseException:
            if attempted:raise MetadataUncertain('Channel metadata outcome requires reconciliation; do not retry') from None
            raise
