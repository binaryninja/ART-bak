import unittest
from contextlib import nullcontext,contextmanager
from dataclasses import FrozenInstanceError
from artex.diagnostics.startup_inventory import *

class DB:
    def __init__(self,rows):self.rows=rows;self.calls=[];self.hook=lambda:None
    def fetch_all(self,q):
        self.calls.append(q);self.hook()
        if isinstance(self.rows,Exception):raise self.rows
        return self.rows

class Tests(unittest.TestCase):
    def make(self,rows,maximum=500):
        self.db=DB(rows);self.allowed=True;self.live=True;self.current_hook=lambda:None
        self.exit_hook=lambda:None
        def current(s):self.current_hook();return self.live
        @contextmanager
        def guard(s):
            try:yield
            finally:self.exit_hook()
        self.reader=StartupInventoryReader(self.db,InventoryScope('fixture'),authorize=lambda s:self.allowed,current=current,lifecycle_guard=guard,max_references=maximum)
        return self.reader
    def test_empty_is_never_authority_and_single_select(self):
        value=self.make([('fixture',0,None,None,None,None)]).read()
        self.assertEqual(value.state,'observed_no_visible_known_work');self.assertFalse(value.ownership_proven or value.launch_authorized or value.recovery_supported)
        self.assertEqual(len(self.db.calls),1);q=self.db.calls[0]
        self.assertTrue(q.sql.startswith('WITH observed'));self.assertNotIn('FOR UPDATE',q.sql);self.assertNotIn('advisory',q.sql)
        with self.assertRaises(FrozenInstanceError):value.launch_authorized=True
        with self.assertRaises(TypeError):StartupInventory('anything',0,launch_authorized=True)
    def test_all_categories_orphan_and_duplicate_task_observations(self):
        cases=[('task_running',5,5,'running'),('task_paused',5,5,'running'),('task_queued',5,5,'running'),('bootstrap',5,7,'prepared'),('worker_claim',None,8,'running'),('archive',5,9,'ready')]
        value=self.make([('fixture',len(cases),*r) for r in cases]).read()
        self.assertEqual(value.state,'observed_records');self.assertEqual(value.total,6);self.assertEqual(len(value.references),6)
        self.assertFalse(value.launch_authorized)
    def test_overflow_unknown_schema_malformed_and_database_mismatch_fail_closed(self):
        fixtures=[RuntimeError('PRIVATE_SCHEMA'),[('foreign',0,None,None,None,None)],[],[('fixture',501,None,None,None,None)],
          [('fixture',2,'bootstrap',5,1,'prepared')],[('fixture',1,'bootstrap',5,1,'unknown')],
          [('fixture',1,'worker_claim',5,True,'running')],[('fixture',0,'bootstrap',5,1,'prepared')],
          [('fixture',1,'invalid_task',5,5,'mystery')], [('fixture',2,'bootstrap',5,1,'prepared')]*2]
        for rows in fixtures:
            value=self.make(rows).read();self.assertEqual(value.state,'unavailable');self.assertIsNone(value.total)
            self.assertEqual(value.references,());self.assertNotIn('PRIVATE',repr(value));self.assertFalse(value.launch_authorized)
    def test_denied_before_sql_and_after_sql_or_guard_exit(self):
        self.make([('fixture',0,None,None,None,None)]);self.allowed=False
        with self.assertRaises(PermissionError):self.reader.read()
        self.assertEqual(self.db.calls,[])
        for stage in ('sql','exit','final_current'):
            self.make([('fixture',0,None,None,None,None)])
            if stage=='sql':self.db.hook=lambda:setattr(self,'allowed',False)
            elif stage=='exit':self.exit_hook=lambda:setattr(self,'allowed',False)
            else:
                count=[0]
                def revoke():
                    count[0]+=1
                    if count[0]==3:self.allowed=False
                self.current_hook=revoke
            with self.assertRaises(PermissionError):self.reader.read()
    def test_unavailable_result_still_needs_final_grant(self):
        self.make(RuntimeError('PRIVATE'));self.exit_hook=lambda:setattr(self,'allowed',False)
        with self.assertRaises(PermissionError):self.reader.read()
    def test_invalid_config_and_binding_mutation(self):
        for maximum in (0,1001,True):
            with self.assertRaises(ValueError):self.make([],maximum)
        with self.assertRaises(ValueError):StartupInventoryReader(DB([]),InventoryScope('x','other'),authorize=lambda s:True,current=lambda s:True,lifecycle_guard=lambda s:nullcontext())
        self.make([]);self.reader._pins=tuple(list(self.reader._pins))
        with self.assertRaises(PermissionError):self.reader.read()
        self.assertEqual(self.db.calls,[])
    def test_sql_does_not_hide_archived_deleted_or_unknown_states(self):
        self.make([('fixture',0,None,None,None,None)]).read();sql=self.db.calls[0].sql
        self.assertNotIn('deleted_at IS NULL',sql);self.assertNotIn('archived_at IS NULL',sql)
        self.assertIn("COALESCE(n.owner,'')<>''",sql);self.assertIn('LEFT JOIN public.tasks',sql)
        self.assertIn("'invalid_task'",sql);self.assertIn('n.state IS NULL',sql)

    def test_deleted_claim_is_known_and_unclaimed_deleted_not_selected(self):
        value=self.make([('fixture',1,'worker_claim',5,8,'deleted')]).read()
        self.assertEqual(value.state,'observed_records')
        self.assertIn("'stopped','deleted'",self.db.calls[0].sql)
    def test_visible_rows_not_schema_or_rls_proof(self):
        value=self.make([('fixture',0,None,None,None,None)]).read()
        self.assertEqual(value.visibility,'database_role_visible_rows')
        self.assertEqual(value.state,'observed_no_visible_known_work')
        self.assertFalse(value.ownership_proven or value.launch_authorized)
    def test_policy_exception_text_is_always_sanitized(self):
        def secret(*args):raise RuntimeError('SECRET_POLICY_VALUE')
        for position in ('initial_auth','initial_current','final_current','final_auth','guard_exit'):
            self.make([('fixture',0,None,None,None,None)])
            if position=='initial_current':self.current_hook=secret
            elif position=='guard_exit':self.exit_hook=lambda:(_ for _ in ()).throw(PermissionError('SECRET_POLICY_VALUE'))
            elif position=='final_current':
                count=[0]
                def hook():
                    count[0]+=1
                    if count[0]==3:secret()
                self.current_hook=hook
            else:
                pins=list(self.reader._original);count=[0]
                def grant(scope):
                    count[0]+=1
                    if position=='initial_auth' or count[0]==4:secret()
                    return True
                pins[2]=grant;self.reader._pins=self.reader._original=tuple(pins)
            with self.assertRaisesRegex(PermissionError,'^Startup inventory access denied$') as caught:self.reader.read()
            self.assertNotIn('SECRET_POLICY_VALUE',str(caught.exception))

    def test_failed_guard_exit_discards_empty_and_populated_snapshot(self):
        for rows in ([('fixture',0,None,None,None,None)],[('fixture',1,'bootstrap',5,7,'prepared')]):
            self.make(rows)
            def fail():raise RuntimeError('PRIVATE_GUARD_FAILURE')
            self.exit_hook=fail
            result=self.reader.read()
            self.assertEqual(result.state,'unavailable');self.assertIsNone(result.total);self.assertEqual(result.references,())
            self.assertNotIn('PRIVATE',repr(result));self.assertFalse(result.launch_authorized)
            self.make(rows)
            def fail_revoke():
                self.allowed=False
                raise RuntimeError('PRIVATE_GUARD_FAILURE')
            self.exit_hook=fail_revoke
            with self.assertRaisesRegex(PermissionError,'^Startup inventory access denied$'):self.reader.read()
