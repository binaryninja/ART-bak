"""Bounded global skill invocation dimensions. No skill loading or archive merge."""
from dataclasses import dataclass
from datetime import datetime, timezone
import unicodedata
from zoneinfo import ZoneInfo
from .core import Query

class UsageUnavailable(RuntimeError): pass

def valid_name(name):
    if type(name) is not str or not 1<=len(name)<=64:return False
    try:name.encode('utf-8')
    except UnicodeError:return False
    def letter(c):return 'a'<=c<='z' or ord(c)>127 and unicodedata.category(c).startswith('L')
    return letter(name[0]) and not name.endswith('-') and '--' not in name and all(letter(c) or '0'<=c<='9' or c=='-' or ord(c)>127 and unicodedata.category(c)=='Nd' for c in name)

@dataclass(frozen=True)
class UsageScope:
    workspace:str
    revision:str
    def validate(self):
        if any(type(v) is not str or not v or len(v.encode('utf-8'))>256 for v in (self.workspace,self.revision)):raise ValueError('Explicit global usage scope required')

@dataclass(frozen=True)
class UsageSelection:
    name:str
    limit:int=50
    def validate(self):
        if not valid_name(self.name) or type(self.limit) is not int or not 1<=self.limit<=200:raise ValueError('Invalid usage selection')

_SQL="""WITH page AS (
 SELECT ts,COALESCE(agent_key,'') agent_key,COALESCE(task_id,0) task_id,
 COALESCE(session_id,'') session_id,args_len FROM skill_usage
 WHERE skill=$1 AND found ORDER BY ts DESC LIMIT $2
), bounded AS (
 SELECT *,sum(octet_length(agent_key)+octet_length(session_id)) OVER () bytes FROM page
) SELECT ts,CASE WHEN bytes<=$3 THEN agent_key END,task_id,
 CASE WHEN bytes<=$3 THEN session_id END,args_len,bytes>$3 FROM bounded ORDER BY ts DESC"""

class SkillUsageReader:
    def __init__(self,executor,scope,*,authorize_global,current,lifecycle_guard,max_bytes=65536):
        if type(scope) is not UsageScope:raise ValueError('Global scope required')
        scope.validate()
        if not callable(getattr(executor,'fetch_all',None)) or any(not callable(c) for c in (authorize_global,current,lifecycle_guard)):raise ValueError('Explicit global authority required')
        if type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Invalid bound')
        self.executor=executor;self.scope=scope;self.authorize_global=authorize_global;self.current=current;self.guard=lifecycle_guard;self.max_bytes=max_bytes
        self._pins=(executor,scope,authorize_global,current,lifecycle_guard,max_bytes)
        self._scope=(scope.workspace,scope.revision)
    def validate(self):
        e,s,a,c,g,b=self._pins
        if self.executor is not e or self.scope is not s or self.authorize_global is not a or self.current is not c or self.guard is not g or type(self.max_bytes) is not int or self.max_bytes!=b or (s.workspace,s.revision)!=self._scope:raise UsageUnavailable()
    def _allow(self,selection,identity,disclosure):
        self.validate();selection.validate()
        if (selection.name,selection.limit)!=identity:raise UsageUnavailable()
        e,s,a,c,g,b=self._pins
        if c(s) is not True:raise PermissionError()
        self.validate()
        if disclosure is not None:disclosure()
        self.validate()
        if c(s) is not True:raise PermissionError()
        self.validate()
        if a(s,selection) is not True:raise PermissionError()
        self.validate();selection.validate()
        if (selection.name,selection.limit)!=identity:raise UsageUnavailable()
    def validate_disclosure(self,selection,*,disclosure=None):
        if type(selection) is not UsageSelection:raise ValueError()
        selection.validate();identity=(selection.name,selection.limit)
        self.validate()
        with self._pins[4]():self._allow(selection,identity,disclosure)
        self._allow(selection,identity,disclosure)
    def read(self,selection,*,disclosure=None):
        if type(selection) is not UsageSelection:raise ValueError()
        selection.validate();identity=(selection.name,selection.limit);self.validate()
        with self._pins[4]():
            self._allow(selection,identity,disclosure)
            rows=self._pins[0].fetch_all(Query(_SQL,(selection.name,selection.limit,self._pins[5])))
            self._allow(selection,identity,disclosure)
            if type(rows) not in (list,tuple) or len(rows)>selection.limit:raise UsageUnavailable()
            out=[];size=0;prior=None
            for row in rows:
                if type(row) not in (tuple,list) or len(row)!=6 or row[5] is not False:raise UsageUnavailable()
                ts,agent,task,session,args,_=row
                if type(ts) is not datetime or type(ts.tzinfo) not in (timezone,ZoneInfo):raise UsageUnavailable()
                if prior is not None and ts>prior:raise UsageUnavailable()
                prior=ts
                if any(type(v) is not str for v in (agent,session)) or any(type(v) is not int or not -2**63<=v<2**63 for v in (task,args)):raise UsageUnavailable()
                size+=len(agent.encode('utf-8'))+len(session.encode('utf-8'))
                if size>self._pins[5]:raise UsageUnavailable()
                stamp=ts.isoformat().replace('+00:00','Z')
                if ts.microsecond:
                    fraction=f'{ts.microsecond:06d}';stamp=stamp.replace('.'+fraction,'.'+fraction.rstrip('0'),1)
                out.append(dict(ts=stamp,agent_key=agent,task_id=task,session_id=session,args_len=args))
            self._allow(selection,identity,disclosure)
        self._allow(selection,identity,disclosure)
        return {'calls':out}
