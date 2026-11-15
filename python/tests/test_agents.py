"""Real catalog store over inert queued SQL results, no DB or seeds at startup."""
from contextlib import contextmanager
from datetime import datetime,timezone
import unittest
from artex.db.agents import AgentStore,AgentInvalid,AgentConflict,AgentMissing

ROW=(1,'custom','Custom','Fixture','assistant',False,True,0,1200,False,False,'',0,'',0,'serial','all',5,None)
class SQL:
    def __init__(self,results=()):self.results=list(results);self.calls=[];self.events=[]
    def query(self,q):
        self.calls.append(q);r=self.results.pop(0)
        if isinstance(r,Exception):raise r
        return r
    fetch_all=query
    fetch_one=query
    execute=query
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:yield self
        except BaseException:self.events.append('rollback');raise
        else:self.events.append('commit')

class AgentStoreTests(unittest.TestCase):
    def test_constructor_inert_and_public_dto(self):
        db=SQL([[ROW],[ROW]]);store=AgentStore(db);self.assertEqual(db.calls,[])
        a=store.list()[0];self.assertEqual(a.to_dict()['id'],'1');self.assertIsNone(a.to_dict()['llm_profile_id'])
        self.assertNotIn('wrapup_prompt',a.to_dict());self.assertEqual(store.get('custom'),a)
        self.assertEqual(db.calls[-1].params,('custom',))
    def test_three_grouped_counts(self):
        db=SQL([[(1,2)],[(1,3)],[('custom',4)]])
        self.assertEqual(AgentStore(db).binding_counts(),({1:2},{1:3},{'custom':4}))
        self.assertEqual(len(db.calls),3)
    def test_create_agent_and_prompt_atomic(self):
        db=SQL([ROW,(7,),1]);a=AgentStore(db).create('custom',' Custom ','description')
        self.assertEqual(a.key,'custom');self.assertEqual(db.events,['begin','commit'])
        self.assertEqual(db.calls[0].params,('custom','Custom','description'))
        self.assertIn('ON CONFLICT (key) DO NOTHING',db.calls[0].sql)
        self.assertIn('Do only what the user asks',db.calls[1].params[1])
        self.assertEqual(db.calls[2].params,(7,1))
    def test_create_seed_failure_rolls_back(self):
        db=SQL([ROW,RuntimeError('fixture')])
        with self.assertRaises(RuntimeError):AgentStore(db).create('custom','Custom')
        self.assertEqual(db.events,['begin','rollback'])
    def test_duplicate_key_does_not_seed(self):
        db=SQL([None])
        with self.assertRaises(AgentConflict):AgentStore(db).create('custom','Custom')
        self.assertEqual(len(db.calls),1);self.assertEqual(db.events,['begin','rollback'])
    def test_driver_no_rows_is_only_mapped_conflict(self):
        from artex.db.postgres import NoRowsError
        db=SQL([NoRowsError('no row')])
        with self.assertRaises(AgentConflict):AgentStore(db).create('custom','Custom')
        self.assertEqual(db.events,['begin','rollback']);self.assertEqual(len(db.calls),1)
        db=SQL([RuntimeError('database down')])
        with self.assertRaisesRegex(RuntimeError,'database down'):AgentStore(db).create('custom','Custom')
        self.assertEqual(db.events,['begin','rollback'])

    def test_metadata_guard_and_validation(self):
        db=SQL([1,0]);store=AgentStore(db)
        store.update_metadata('custom',' New ','')
        self.assertIn('builtin=false',db.calls[0].sql)
        self.assertEqual(db.calls[0].params,('custom','New',''))
        with self.assertRaises(AgentMissing):store.update_metadata('custom','New')
        for bad in ('../x','Upper','a-b','x'*129):
            with self.assertRaises(AgentInvalid):store.get(bad)
        self.assertEqual(len(db.calls),2)
    def test_persisted_details_bound_and_omitempty(self):
        db=SQL([[('template',)],[('Goal','Goal info','example','task')],[(2,'template','',datetime(2026,1,1,tzinfo=timezone.utc))],[],[],[(3,'Public name','model',True)]])
        from artex.db.agents import Agent
        details=AgentStore(db).detail(Agent(*ROW))
        self.assertEqual(details['visibility'],{'mcp':None,'skill':[]})
        self.assertNotIn('note',details['versions'][0]);self.assertEqual(details['prompt'],'template')
        self.assertEqual(details['llm_profiles'],[{'id':3,'name':'Public name','model':'model','is_default':True}])
        self.assertTrue(all('api_key' not in q.sql for q in db.calls))
