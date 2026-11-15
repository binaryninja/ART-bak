import asyncio,unittest
from contextlib import contextmanager
from artex.db.catalog_seed import *
from artex.db.postgres import NoRowsError
class Fake:
    def __init__(self,rows):self.rows=list(rows);self.calls=[];self.events=[];self.affected=1
    def fetch_one(self,q):
        self.calls.append(q);value=self.rows.pop(0)
        if isinstance(value,BaseException):raise value
        return value
    def execute(self,q):self.calls.append(q);return self.affected
    @contextmanager
    def transaction(self):
        self.events.append('begin')
        try:yield self
        except BaseException:self.events.append('rollback');raise
        else:self.events.append('commit')
class CatalogTests(unittest.TestCase):
    def test_all_keys_roles_defaults_and_preserving_conflicts(self):
        db=Fake([(None,)]+[(i+1,True,a[2]) for i,a in enumerate(BUILTINS)])
        result=seed_builtin_catalog(db,action='seed_missing')
        self.assertEqual(list(result.agent_ids),['goals','planner','mainagent','worker','auto','pentest'])
        self.assertFalse(result.runtime_ready);self.assertFalse(result.default_prompt_bodies_seeded)
        inserts=[q for q in db.calls if q.sql.startswith('INSERT')]
        self.assertEqual(len(inserts),14)
        self.assertTrue(all('DO NOTHING' in q.sql for q in inserts))
        self.assertFalse(any('DELETE' in q.sql or 'UPDATE agents SET' in q.sql for q in db.calls))
        pentest=[q for q in inserts if q.params[0]=='pentest'][0]
        self.assertEqual(pentest.params[-2:],(True,0));self.assertEqual(db.events,['begin','commit'])
    def test_conflicting_user_agent_rolls_back_not_claimed(self):
        db=Fake([(None,),(1,False,'goals')])
        with self.assertRaises(CatalogSeedUnavailable):seed_builtin_catalog(db,action='seed_missing')
        self.assertEqual(db.events,['begin','rollback'])
    def test_prompt_preserves_current_even_empty_user_prompt(self):
        db=Fake([(None,),(88,)])
        self.assertIsNone(seed_prompt_if_empty(db,1,'new',action='seed_missing'))
        self.assertEqual(len(db.calls),2);self.assertEqual(db.events[-1],'commit')
    def test_prompt_version_insert_and_pointer_atomic(self):
        db=Fake([(None,),(None,),(4,),(9,)])
        self.assertEqual(seed_prompt_if_empty(db,1,'reviewed template',action='seed_missing'),4)
        self.assertIn('FOR UPDATE',db.calls[1].sql)
        self.assertEqual(db.calls[3].params,(1,4,'reviewed template','Builtin default','system'))
        self.assertEqual(db.calls[4].params,(9,1));self.assertEqual(db.events[-1],'commit')
    def test_missing_agent_and_cancel_propagate_with_rollback(self):
        for error,expected in ((NoRowsError(),CatalogSeedMissing),(asyncio.CancelledError(),asyncio.CancelledError),(RuntimeError('fixture'),RuntimeError)):
            db=Fake([(None,),error])
            with self.assertRaises(expected):seed_prompt_if_empty(db,1,'template',action='seed_missing')
            self.assertEqual(db.events[-1],'rollback')
    def test_failed_pointer_rolls_back_insert(self):
        db=Fake([(None,),(None,),(1,),(9,)]);db.affected=0
        with self.assertRaises(CatalogSeedUnavailable):seed_prompt_if_empty(db,1,'template',action='seed_missing')
        self.assertEqual(db.events[-1],'rollback')
    def test_optin_and_empty_template_before_io(self):
        db=Fake([])
        with self.assertRaises(CatalogSeedInvalid):seed_builtin_catalog(db)
        with self.assertRaises(CatalogSeedInvalid):seed_prompt_if_empty(db,1,'',action='seed_missing')
        self.assertEqual(db.calls,[]);self.assertEqual(db.events,[])
