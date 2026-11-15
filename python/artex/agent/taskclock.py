"""Pure taskclock.go policy, durations expressed in seconds (not nanoseconds).

No timer, cancellation, provider call or implicit budget extension. A deadline
floors remaining time at one second exactly as Go does: this avoids zero meaning
unlimited in its harness, NOT permission to execute after a task expires. Host
lifecycle authority must still reject expired work. Current TaskEngine rejects
nonzero task timeout; connecting this policy to execution/settlement is separate.
"""
from dataclasses import dataclass
from collections.abc import Mapping
import math
from ..db.core import int64

_KEY='artex.task_clock'

@dataclass(frozen=True)
class TaskClock:
    deadline_unix:int=0
    final:bool=False
    def __post_init__(self):
        int64(self.deadline_unix,'deadline_unix')
        if type(self.final) is not bool:raise ValueError('final must be boolean')


def with_task_clock(context,clock):
    """Return a copied metadata mapping; never mutate the caller's context."""
    if not isinstance(context,Mapping) or not isinstance(clock,TaskClock):raise ValueError('Context mapping and TaskClock required')
    return dict(context,**{_KEY:clock})


def task_clock_from(context):
    if not isinstance(context,Mapping):raise ValueError('Context mapping required')
    clock=context.get(_KEY)
    return clock if isinstance(clock,TaskClock) else TaskClock()


def _seconds(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError(name+' must be finite seconds')
    return value


def clamp_max_duration(deadline_unix,own_budget,*,wall_clock=None):
    """Return (effective_seconds, task_clamped), preserving source ordering.

    own_budget <= 0 is unlimited when a deadline exists; without a deadline it
    is returned unchanged. If the own positive budget is below one second, it
    still wins over the floored remaining value. Equality counts as task-bound.
    wall_clock is an explicit callable returning finite Unix seconds, required
    only when deadline_unix > 0 and invoked exactly once. No default wall clock.
    """
    int64(deadline_unix,'deadline_unix');_seconds(own_budget,'own_budget')
    if deadline_unix<=0:return own_budget,False
    if not callable(wall_clock):raise ValueError('Explicit wall clock required')
    now=_seconds(wall_clock(),'wall clock')
    remaining=max(1.0,deadline_unix-now)
    if not math.isfinite(remaining):raise ValueError('Remaining duration is not finite')
    clamped=own_budget<=0 or remaining<=own_budget
    effective=own_budget if 0<own_budget<remaining else remaining
    return effective,clamped
