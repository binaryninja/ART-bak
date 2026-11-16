import json
import unittest
from contextlib import contextmanager, nullcontext
from artex.db.missing_skill_stats import MissingSkillStatsReader, MissingStatsSelection, MissingStatsUnavailable
from artex.db.skill_usage_read import UsageScope


def summary(name, calls, **kwargs):
    return json.dumps({'missing_skill_stats':[dict(skill=name,calls=calls,**kwargs)]})


class Executor:
    def __init__(self, rows): self.rows=rows; self.calls=[]; self.hook=lambda:None
    def fetch_all(self,query):
        self.calls.append(query);self.hook();return self.rows


class MissingSkillStatsTests(unittest.TestCase):
    def make(self, rows=(), **kwargs):
        e=Executor(rows);self.active=True
        r=MissingSkillStatsReader(e,UsageScope('workspace','revision'),
            authorize_global=kwargs.pop('authorize_global',lambda s,x:self.active),
            current=lambda s:self.active,lifecycle_guard=kwargs.pop('lifecycle_guard',nullcontext),**kwargs)
        return r,e

    def test_empty_and_single_statement(self):
        r,e=self.make()
        self.assertEqual(r.read(MissingStatsSelection()), {'missing':[]})
        self.assertEqual(len(e.calls),1)
        self.assertIn('task.archived_at IS NOT NULL',e.calls[0].sql)
        self.assertIn('WHERE NOT found',e.calls[0].sql)

    def test_cold_merge_before_limit(self):
        r,e=self.make([(0,summary('a',2,agents=['worker']),False),
                      (0,summary('b',4),False),
                      (1,summary('a',5,agents=['planner'],last_used='2026-01-01T12:00:00Z'),False)])
        out=r.read(MissingStatsSelection(1))['missing']
        self.assertEqual(out,[dict(skill='a',calls=7,tasks=0,agents=['worker','planner'],last_used='2026-01-01T12:00:00Z')])

    def test_legacy_successes_not_missing(self):
        r,e=self.make([(1,'{"skills":{"a":90}}',False)])
        self.assertEqual(r.read(MissingStatsSelection()),{'missing':[]})

    def test_timestamp_fraction_and_duplicate_hot(self):
        r,e=self.make([(0,summary('a',1,last_used='2026-01-01T12:00:00.123000Z'),False)])
        self.assertEqual(r.read(MissingStatsSelection())['missing'][0]['last_used'],'2026-01-01T12:00:00.123Z')
        r,e=self.make([(0,summary('a',1),False)]*2)
        with self.assertRaises(MissingStatsUnavailable):r.read(MissingStatsSelection())

    def test_authority_required_before_sql(self):
        r,e=self.make();self.active=False
        with self.assertRaises(PermissionError):r.read(MissingStatsSelection())
        self.assertEqual(e.calls,[])

    def test_revoked_during_fetch(self):
        r,e=self.make([(0,summary('secret',1),False)])
        e.hook=lambda:setattr(self,'active',False)
        with self.assertRaises(PermissionError):r.read(MissingStatsSelection())

    def test_guard_exit_revocation(self):
        @contextmanager
        def guard():
            yield
            self.active=False
        r,e=self.make([(0,summary('secret',1),False)],lifecycle_guard=guard)
        with self.assertRaises(PermissionError):r.read(MissingStatsSelection())

    def test_request_disclosure_final_fence(self):
        r,e=self.make()
        calls=[]
        def disclose():calls.append(1)
        r.read(MissingStatsSelection(),disclosure=disclose)
        self.assertGreaterEqual(len(calls),4)
        self.active=False
        with self.assertRaises(PermissionError):r.validate_disclosure(MissingStatsSelection(),disclosure=disclose)

    def test_pin_replacement_during_fetch(self):
        r,e=self.make();e.hook=lambda:setattr(r,'authorize_global',lambda s,x:True)
        with self.assertRaises(MissingStatsUnavailable):r.read(MissingStatsSelection())

    def test_scope_mutation_during_fetch(self):
        r,e=self.make();e.hook=lambda:object.__setattr__(r.scope,'revision','other')
        with self.assertRaises(MissingStatsUnavailable):r.read(MissingStatsSelection())

    def test_last_current_revokes_global_grant(self):
        state={'current_calls':0,'allowed':True}
        def current(scope):
            state['current_calls']+=1
            if state['current_calls']==8:state['allowed']=False
            return True
        e=Executor([(0,summary('secret',1),False)])
        r=MissingSkillStatsReader(e,UsageScope('workspace','revision'),
            authorize_global=lambda s,x:state['allowed'],current=current,lifecycle_guard=nullcontext)
        with self.assertRaises(PermissionError):r.read(MissingStatsSelection())
        self.assertEqual(state['current_calls'],8)

    def test_last_current_revokes_disclosure(self):
        state={'current_calls':0,'disclose':True}
        def current(scope):
            state['current_calls']+=1
            if state['current_calls']==8:state['disclose']=False
            return True
        def disclosure():
            if not state['disclose']:raise PermissionError('Disclosure revoked')
        e=Executor([(0,summary('secret',1),False)])
        r=MissingSkillStatsReader(e,UsageScope('workspace','revision'),
            authorize_global=lambda s,x:True,current=current,lifecycle_guard=nullcontext)
        with self.assertRaisesRegex(PermissionError,'Disclosure revoked'):
            r.read(MissingStatsSelection(),disclosure=disclosure)
        self.assertEqual(state['current_calls'],8)

    def test_transfer_bounds_and_sql_sentinel(self):
        for rows,kwargs in (([(0,None,True)],{}),
                            ([(0,summary('a',1),False)]*2,{'max_rows':1}),
                            ([(1,summary('x'*1100,1),False)],{'max_bytes':1024})):
            r,e=self.make(rows,**kwargs)
            with self.assertRaises(MissingStatsUnavailable):r.read(MissingStatsSelection())

    def test_nested_stat_row_bound(self):
        raw=json.dumps({'missing_skill_stats':[{'skill':'a'},{'skill':'b'}]})
        r,e=self.make([(1,raw,False)],max_rows=1)
        with self.assertRaises(ValueError):r.read(MissingStatsSelection())

    def test_selection_bounds(self):
        r,e=self.make()
        for limit in (True,0,201,'1'):
            with self.assertRaises(ValueError):r.read(MissingStatsSelection(limit))
        self.assertEqual(e.calls,[])

if __name__=='__main__':unittest.main()
