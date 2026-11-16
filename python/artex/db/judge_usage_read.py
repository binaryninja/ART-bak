"""Global judge ledger aggregates only; no raw records, invocation or writes.

Source db/llm_usage.go JudgeUsageStats. A single statement snapshots cumulative
and recent series consistently; days limits only the latter. No archive merging.
"""
from datetime import date
from .core import Query

class UsageUnavailable(RuntimeError):pass

def normalized_days(value):
    if type(value) is not int:raise ValueError('Integer days required')
    if value<=0:return 30
    if value>366:raise ValueError('Daily range exceeds bound')
    return value

_SQL="""WITH totals AS (
 SELECT COUNT(*) AS calls,COALESCE(SUM(input_tokens),0) AS input_tokens,
 COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(cache_read),0) AS cache_read_tokens,
 COALESCE(SUM(cache_write),0) AS cache_write_tokens FROM llm_usage WHERE worker='judge'
), daily AS (
 SELECT to_char(ts AT TIME ZONE 'UTC','YYYY-MM-DD') AS day,COUNT(*) AS calls,
 COALESCE(SUM(input_tokens),0) AS input_tokens,COALESCE(SUM(output_tokens),0) AS output_tokens
 FROM llm_usage WHERE worker='judge' AND ts>=now()-($1 * interval '1 day')
 GROUP BY day ORDER BY day LIMIT $2
) SELECT t.calls,t.input_tokens,t.output_tokens,t.cache_read_tokens,t.cache_write_tokens,
 d.day,d.calls,d.input_tokens,d.output_tokens FROM totals t LEFT JOIN daily d ON true ORDER BY d.day"""

class JudgeUsageReader:
    def __init__(self,executor):
        if not callable(getattr(executor,'fetch_all',None)):raise ValueError('Explicit executor required')
        self.executor=executor;self._executor=executor
    def validate(self):
        if self.executor is not self._executor:raise UsageUnavailable()
    def read(self,days):
        days=normalized_days(days);self.validate()
        rows=self._executor.fetch_all(Query(_SQL,(days,368)));self.validate()
        if type(rows) not in (list,tuple) or not rows or len(rows)>367:raise UsageUnavailable()
        totals=None;daily=[];prior=''
        for row in rows:
            if type(row) not in (list,tuple) or len(row)!=9:raise UsageUnavailable()
            head=tuple(row[:5])
            if any(type(n) is not int or not -2**63<=n<2**63 for n in head) or head[0]<0:raise UsageUnavailable()
            if totals is not None and totals!=head:raise UsageUnavailable()
            totals=head
            if row[5] is None:
                if len(rows)!=1 or any(v is not None for v in row[6:]):raise UsageUnavailable()
                continue
            day=row[5]
            if type(day) is not str or len(day)!=10 or date.fromisoformat(day).isoformat()!=day or day<=prior:raise UsageUnavailable()
            prior=day
            if any(type(n) is not int or not -2**63<=n<2**63 for n in row[6:]) or row[6]<0:raise UsageUnavailable()
            daily.append(dict(zip(('date','calls','input_tokens','output_tokens'),row[5:])))
        return dict(zip(('calls','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'),totals),daily=daily)
