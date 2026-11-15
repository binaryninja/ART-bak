"""Protected GET /api/stats, composing real aggregates and explicit live state.

runtime_snapshot(selector) must resolve exactly the selected authorized live task;
selector is 'active' for omitted/empty/active and canonical decimal ID otherwise.
It returns RuntimeStats with optional TaskRuntimeStats. Missing adapter is 503,
not an invented idle engine. A None task for explicit ID is 404; a different task
is a failed adapter (503), never a fallback to active. Traffic None means capture
is absent; 0 means enabled and empty, not a database failure. Upstream silently
omits or zeros some failed reads; this port fails closed instead. There is no
atomicity across runtime and DB snapshots. Nothing starts/resumes tasks/providers.
"""
from dataclasses import dataclass
import math
import re
import time
from urllib.parse import parse_qs,urlsplit
from .core import json_response,error_response
from ..db.stats import StatsInvalid,StatsUnavailable,_count


@dataclass(frozen=True)
class TaskRuntimeStats:
    id:str
    description:str
    goal:str
    ready:bool
    started:bool
    paused:bool
    active_calls:int
    last_activity:int


@dataclass(frozen=True)
class RuntimeStats:
    llm_configured:bool
    task:TaskRuntimeStats|None=None
    traffic_count:int|None=None


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,19}',value) or not 0<int(value)<2**63:
        raise StatsInvalid('Invalid task id')
    return str(int(value))


def _runtime(value):
    if not isinstance(value,RuntimeStats) or type(value.llm_configured) is not bool:
        raise StatsUnavailable('Invalid runtime snapshot')
    if value.traffic_count is not None:_count(value.traffic_count)
    task=value.task
    if task is not None:
        if not isinstance(task,TaskRuntimeStats):raise StatsUnavailable('Invalid runtime task')
        try:_id(task.id)
        except ValueError:raise StatsUnavailable('Invalid runtime task id') from None
        if not isinstance(task.description,str) or not isinstance(task.goal,str):raise StatsUnavailable('Invalid runtime metadata')
        if any(type(v) is not bool for v in (task.ready,task.started,task.paused)):raise StatsUnavailable('Invalid runtime state')
        _count(task.active_calls);_count(task.last_activity)
    return value


class StatsService:
    def __init__(self,store=None,*,runtime_snapshot=None,clock=time.time):
        self.store,self.runtime_snapshot,self.clock=store,runtime_snapshot,clock

    def register(self,router):
        router.add('GET','/api/stats',lambda request,params:self.get(request))

    def get(self,request):
        try:
            q=parse_qs(urlsplit(request.target).query,keep_blank_values=True,max_num_fields=1)
            if set(q)-{'task'} or any(len(v)!=1 for v in q.values()):raise StatsInvalid('Invalid selectors')
            selector=q.get('task',['active'])[0]
            selector='active' if selector in ('','active') else _id(selector)
            if self.store is None or not callable(self.runtime_snapshot):raise StatsUnavailable('Runtime/DB adapter missing')
            live=_runtime(self.runtime_snapshot(selector))
            task=live.task
            if selector!='active':
                if task is None:return error_response(404,'Task not found')
                if _id(task.id)!=selector:raise StatsUnavailable('Runtime task scope mismatch')
            assets=self.store.asset_counts()
            # Store aggregate dictionary must not leak arbitrary adapter objects.
            if not isinstance(assets,dict) or any(not isinstance(k,str) for k in assets):raise StatsUnavailable('Invalid asset aggregate')
            for count in assets.values():_count(count)
            out={'engine_mode':'idle','llm_configured':live.llm_configured,
                 'assets':sum(assets.values()),'asset_counts':assets}
            if live.traffic_count is not None:out.update(traffic=live.traffic_count,traffic_enabled=True)
            if task is None:return json_response(200,out)
            counts=self.store.task_counts(int(task.id))
            if counts is None:return error_response(404,'Task not found')
            if not isinstance(counts,dict) or set(counts)!={'exploration','in_flight','goals_total','goals_met'}:
                raise StatsUnavailable('Invalid task aggregate')
            if not isinstance(counts['exploration'],dict):raise StatsUnavailable('Invalid exploration aggregate')
            if any(not isinstance(k,str) for k in counts['exploration']):raise StatsUnavailable('Invalid exploration aggregate')
            for value in counts['exploration'].values():_count(value)
            for key in ('in_flight','goals_total','goals_met'):_count(counts[key])
            if counts['goals_met']>counts['goals_total']:raise StatsUnavailable('Invalid goal counts')
            now=self.clock()
            if type(now) not in (int,float) or not math.isfinite(now):raise StatsUnavailable('Invalid clock')
            running=task.ready and task.started and not task.paused
            stalled=running and task.active_calls==0 and counts['in_flight']>0 and task.last_activity>0 and int(now)-task.last_activity>120
            mode='paused' if task.paused else 'stalled' if stalled else 'exploring' if running and task.active_calls>0 else 'idle'
            out.update(engine_mode=mode,llm_configured=task.ready,exploration=counts['exploration'])
            out['active_task']={'id':task.id,'description':task.description,'goal':task.goal,'running':running,
                'paused':task.paused,'engine_mode':mode,'in_flight':counts['in_flight'],'llm_in_flight':task.active_calls,
                'last_activity':task.last_activity,'stalled':stalled,'goals_total':counts['goals_total'],'goals_met':counts['goals_met']}
            return json_response(200,out)
        except StatsUnavailable:return error_response(503,'Dashboard dependency unavailable or invalid')
        except (ValueError,UnicodeError):return error_response(400,'Invalid dashboard request')
        except Exception:return error_response(500,'Dashboard read failed')
