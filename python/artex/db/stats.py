"""Read-only dashboard aggregates from server.stats / ExplorationStore.Stats.

Each query is a separate snapshot, matching upstream's nontransactional dashboard.
Counts include all exploration kinds/states, not only visible nodes. Running
intent and goal counters preserve Go's newest-100000/newest-10000 caps before
aggregation. Assets are GLOBAL even when a task is selected; global aggregation
requires explicit allow_global=True. Task/exploration ownership is resolved from
persisted live tasks rather than inferred from a caller-supplied exploration id.
Both deleted_at and archived_at must be NULL. This deliberately tightens Go
ListTasks' deleted-only check: archive completion normally sets both, but a stale
runtime adapter must not expose pruned counts from an inconsistent archived row.
No database failure becomes a zero/empty success. No traffic/provider operations.
"""
from .core import Query,int64


class StatsUnavailable(RuntimeError):pass
class StatsInvalid(ValueError):pass


def _count(value):
    if type(value) is not int or not 0<=value<2**63:raise StatsUnavailable('Invalid count result')
    return value


def counts(rows):
    result={}
    for row in rows:
        if len(row)!=2 or not isinstance(row[0],str) or row[0] in result:
            raise StatsUnavailable('Invalid aggregate row')
        result[row[0]]=_count(row[1])
    return result


class StatsStore:
    def __init__(self,executor,*,allow_global=False):
        if type(allow_global) is not bool:raise StatsInvalid('allow_global must be boolean')
        self.executor,self.allow_global=executor,allow_global

    def asset_counts(self):
        if not self.allow_global:raise StatsUnavailable('Global dashboard counts not enabled')
        return counts(self.executor.fetch_all(Query('SELECT type, COUNT(*) FROM assets GROUP BY type')))

    def task_counts(self,task_id):
        if int64(task_id,'task id')<=0:raise StatsInvalid('Task id must be positive')
        rows=self.executor.fetch_all(Query('SELECT exploration_id FROM tasks WHERE id=$1 AND deleted_at IS NULL AND archived_at IS NULL',(task_id,)))
        if not rows:return None
        if len(rows)!=1 or len(rows[0])!=1 or type(rows[0][0]) is not int or not 0<rows[0][0]<2**63:
            raise StatsUnavailable('Invalid task exploration mapping')
        exp=rows[0][0]
        kinds=counts(self.executor.fetch_all(Query('SELECT kind, count(*) FROM exploration_nodes WHERE exploration_id=$1 GROUP BY kind',(exp,))))
        row=self.executor.fetch_one(Query("SELECT count(*) FILTER (WHERE state='running') FROM (SELECT state FROM exploration_nodes WHERE exploration_id=$1 AND kind=$2 ORDER BY id DESC LIMIT $3) selected",(exp,'intent',100000)))
        if len(row)!=1:raise StatsUnavailable('Invalid intent aggregate')
        running=_count(row[0])
        row=self.executor.fetch_one(Query("SELECT count(*), count(*) FILTER (WHERE state='met') FROM (SELECT state FROM exploration_nodes WHERE exploration_id=$1 AND kind=$2 ORDER BY id DESC LIMIT $3) selected",(exp,'goal',10000)))
        if len(row)!=2:raise StatsUnavailable('Invalid goal aggregate')
        total,met=map(_count,row)
        if running>100000 or total>10000 or met>total:raise StatsUnavailable('Invalid bounded aggregate')
        return {'exploration':kinds,'in_flight':running,'goals_total':total,'goals_met':met}
