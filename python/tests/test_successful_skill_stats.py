import json,unittest
from contextlib import nullcontext,contextmanager
from artex.db.successful_skill_stats import SuccessfulSkillStatsReader,SuccessfulStatsSelection,SuccessfulStatsUnavailable
from artex.db.skill_usage_read import UsageScope
from test_missing_skill_stats import Executor

def summary(name,calls,**kw):return json.dumps({'skill_stats':[dict(skill=name,calls=calls,**kw)]})

class Tests(unittest.TestCase):
    def make(self,rows=(),**kw):
        self.allowed=self.current=True;self.hook=lambda:None
        def current(s):self.hook();return self.current
        self.db=Executor(rows)
        return SuccessfulSkillStatsReader(self.db,UsageScope('workspace','revision'),
            authorize_global=lambda *a:self.allowed,current=current,lifecycle_guard=kw.pop('lifecycle_guard',nullcontext),**kw)
    def test_single_statement_found_distinct_tasks(self):
        r=self.make();self.assertEqual(r.read(SuccessfulStatsSelection()),{'stats':[]})
        self.assertEqual(len(self.db.calls),1);sql=self.db.calls[0].sql
        for text in ('WHERE found','COUNT(DISTINCT task_id)','task.archived_at IS NOT NULL','LIMIT $1','total_bytes<=$2'):self.assertIn(text,sql)
    def test_successful_legacy_and_hot_cold(self):
        r=self.make([(0,summary('sample',2,tasks=1,agents=['worker']),False),
            (1,summary('sample',3,tasks=2,agents=['planner'],last_used='2026-01-01T00:00:00.120000Z'),False),
            (1,'{"skills":{"sample":4,"cold":1}}',False),
            (1,'{"missing_skill_stats":[{"skill":"missing","calls":999}]}',False)])
        self.assertEqual(r.read(SuccessfulStatsSelection())['stats'],[
            dict(skill='sample',calls=9,tasks=4,agents=['worker','planner'],last_used='2026-01-01T00:00:00.12Z'),
            dict(skill='cold',calls=1,tasks=1,agents=[],last_used=None)])
    def test_no_top_n_truncation(self):
        r=self.make([(0,summary(str(i),1),False) for i in range(25)])
        self.assertEqual(len(r.read(SuccessfulStatsSelection())['stats']),25)
    def test_denied_before_sql_and_during_fetch(self):
        r=self.make();self.allowed=False
        with self.assertRaises(PermissionError):r.read(SuccessfulStatsSelection())
        self.assertFalse(self.db.calls);self.allowed=True
        self.db.hook=lambda:setattr(self,'allowed',False)
        with self.assertRaises(PermissionError):r.read(SuccessfulStatsSelection())
    def test_bounds_and_legacy_count(self):
        for rows in ([(0,summary('x',1),False)]*2,[(0,None,True)],[(1,'{"skills":{"a":1,"b":2}}',False)]):
            r=self.make(rows,max_rows=1)
            with self.assertRaises(SuccessfulStatsUnavailable):r.read(SuccessfulStatsSelection())
    def test_duplicate_and_invalid_hot(self):
        for rows in ([(0,summary('x',1),False)]*2,[(0,'{"skills":{"x":1}}',False)],[(0,'{"missing_skill_stats":[{"skill":"x","calls":1}]}',False)]):
            r=self.make(rows)
            with self.assertRaises(SuccessfulStatsUnavailable):r.read(SuccessfulStatsSelection())
    def test_overflow_and_malformed(self):
        for rows in ([(0,summary('x',2**63-1),False),(1,summary('x',1),False)],[(1,'{bad',False)]):
            r=self.make(rows)
            with self.assertRaises(SuccessfulStatsUnavailable):r.read(SuccessfulStatsSelection())
    def test_final_current_revokes_global(self):
        r=self.make();calls=[0]
        def current():
            calls[0]+=1
            if calls[0]==8:self.allowed=False
        self.hook=current
        with self.assertRaises(PermissionError):r.read(SuccessfulStatsSelection())
    def test_guard_exit_revocation(self):
        @contextmanager
        def guard():
            yield
            self.allowed=False
        r=self.make(lifecycle_guard=guard)
        with self.assertRaises(PermissionError):r.read(SuccessfulStatsSelection())

if __name__=='__main__':unittest.main()
