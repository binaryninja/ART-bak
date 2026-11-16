"""Read-only global tool-input rules; NOT asset/task intercept rules.

Source db/intercept.go ListInterceptRules. No matching, cache activation or writes.
One SQL snapshot suppresses text transfer on cumulative overflow; never truncates.
"""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import re
from .core import Query

FIELDS=('id','name','enabled','priority','match_target','match_type','pattern','action','message','timeout_enabled','timeout_seconds','timeout_action','created_at','updated_at')
TEXT=('name','match_target','match_type','pattern','action','message','timeout_action')
class RulesUnavailable(RuntimeError):pass

class ToolInterceptReader:
    def __init__(self,executor,*,max_rows=1000,max_bytes=1048576):
        if not callable(getattr(executor,'fetch_all',None)):raise ValueError('Explicit executor required')
        if type(max_rows) is not int or not 1<=max_rows<=10000 or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Finite bounds required')
        self.executor=executor;self.max_rows=max_rows;self.max_bytes=max_bytes;self._pins=(executor,max_rows,max_bytes)
    def validate(self):
        e,r,b=self._pins
        if self.executor is not e or self.max_rows!=r or self.max_bytes!=b:raise RulesUnavailable()
    def read(self):
        self.validate()
        columns=','.join('CASE WHEN bytes<=$2 THEN '+f+' ELSE NULL END AS '+f if f in TEXT else f for f in FIELDS)
        total='+'.join('octet_length('+f+')' for f in TEXT)
        query=Query('WITH bounded AS (SELECT '+','.join(FIELDS)+' FROM intercept_rules ORDER BY priority DESC,id LIMIT $1), measured AS (SELECT *,SUM('+total+') OVER () AS bytes FROM bounded) SELECT '+columns+' FROM measured ORDER BY priority DESC,id',(self.max_rows+1,self.max_bytes))
        rows=self._pins[0].fetch_all(query);self.validate()
        if type(rows) not in (list,tuple) or len(rows)>self.max_rows:raise RulesUnavailable()
        result=[];used=0
        for row in rows:
            if type(row) not in (list,tuple) or len(row)!=len(FIELDS):raise RulesUnavailable()
            d=dict(zip(FIELDS,row))
            for key in TEXT:
                if type(d[key]) is not str:raise RulesUnavailable()
                used+=len(d[key].encode('utf-8'))
            if used>self.max_bytes:raise RulesUnavailable()
            if type(d['id']) is not int or not 0<d['id']<2**63:raise RulesUnavailable()
            for key in ('priority','timeout_seconds'):
                if type(d[key]) is not int or not -2**31<=d[key]<2**31:raise RulesUnavailable()
            for key in ('enabled','timeout_enabled'):
                if type(d[key]) is not bool:raise RulesUnavailable()
            for key in ('created_at','updated_at'):
                value=d[key]
                if type(value) is not datetime or type(value.tzinfo) not in (timezone,ZoneInfo):raise RulesUnavailable()
                encoded=value.isoformat()
                if len(encoded)>40 or value.utcoffset() is None:raise RulesUnavailable()
                d[key]=re.sub(r'(\.\d*?[1-9])0+(?=[+-]|$)',r'\1',encoded).replace('+00:00','Z')
            result.append(d)
        return result
