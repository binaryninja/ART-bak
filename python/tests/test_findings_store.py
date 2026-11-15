from contextlib import contextmanager
from datetime import datetime,timezone
import json
import unittest
from artex.db.findings import FindingStore,FindingFilter,FindingInvalid,EvidenceConflict,FindingArchiveState

NOW=datetime(2026,11,15,tzinfo=timezone.utc)
ROW=(1,10,20,'xss','Name','high','Summary','Evidence','worker','[4,5]','pending',NOW,'Task',3,2,1)

class Fake:
    def __init__(self,results=()):self.results=list(results);self.calls=[]
    def call(self,mode,q):
        self.calls.append((mode,q));result=self.results.pop(0)
        if isinstance(result,Exception):raise result
        return result
    def fetch_one(self,q):return self.call('one',q)
    def fetch_all(self,q):return self.call('all',q)
    def execute(self,q):return self.call('execute',q)
    @contextmanager
    def transaction(self):
        self.calls.append(('begin',None))
        try:yield self
        except BaseException:
            self.calls.append(('rollback',None));raise
        else:self.calls.append(('commit',None))

class FindingTests(unittest.TestCase):
    def test_list_get_complete_hydration_scopes(self):
        db=Fake([[ROW],[ROW+('Report',)],[]]);store=FindingStore(db,task_id=10,exploration_id=30)
        finding=store.list()[0]
        self.assertEqual(finding.asset_ids,[4,5]);self.assertEqual(finding.traffic_count,1)
        self.assertEqual(finding.report_evidence_version,2)
        self.assertEqual(store.get(1).report,'Report');self.assertIsNone(store.get(99))
        self.assertEqual(db.calls[0][1].params,(10,30,500))
        self.assertEqual(db.calls[1][1].params,(1,10,30))
        self.assertIn('scope_node.exploration_id=$3',db.calls[1][1].sql)
        self.assertEqual(finding.to_dict()['created_at'],NOW.isoformat())

    def test_filter_literal_query_bound_pagination(self):
        db=Fake([(21,),[ROW]])
        rows,total=FindingStore(db).list_page(FindingFilter(severity='high',status='pending',task_id='10',query=r'a%_\b',sort='severity'),page=2,page_size=20)
        self.assertEqual(total,21);self.assertEqual(rows[0].id,1)
        self.assertEqual(db.calls[0][1].params,('high','pending',10,r'%a\%\_\\b%'))
        self.assertEqual(db.calls[1][1].params[-2:],(20,20))
        self.assertIn('LIMIT $5 OFFSET $6',db.calls[1][1].sql)
        self.assertIn("WHEN 'critical' THEN 4",db.calls[1][1].sql)
        self.assertNotIn('a%',db.calls[0][1].sql)

    def test_unassigned_unknown_asset_scope_and_out_of_range(self):
        db=Fake([(0,)]);store=FindingStore(db)
        self.assertEqual(store.list_page(FindingFilter(task_id='__unassigned__',asset_scope='__none__'),page=999999),( [],0))
        self.assertIn('f.task_id IS NULL OR t.id IS NULL',db.calls[0][1].sql)
        self.assertIn('JOIN assets a',db.calls[0][1].sql)
        with self.assertRaises(NotImplementedError):store.list_page(FindingFilter(asset_scope='a:1'))
        self.assertEqual(len(db.calls),1)

    def test_explicit_invalid_task_filter_rejected_before_query(self):
        for value in ('bad', '0', '-1', '9223372036854775808', '1 OR 1=1',
                      ' 1', '１', None, False, 0, 1.5):
            db=Fake()
            with self.subTest(value=value):
                with self.assertRaises(FindingInvalid):
                    FindingStore(db).list_page(FindingFilter(task_id=value))
                self.assertEqual(db.calls,[])
        for value in ('', '__unassigned__', '1', 1):
            db=Fake([(0,)])
            FindingStore(db).list_page(FindingFilter(task_id=value))
            self.assertEqual(len(db.calls),1)
            if value in ('1',1):self.assertEqual(db.calls[0][1].params,(1,))

    def test_create_scoped_node_validation_and_binding(self):
        db=Fake([(None,),[(None,)],[],[(20,)],(1,)])
        id=FindingStore(db,task_id=10,exploration_id=30).create(task_id=10,node_id=20,severity='high',name="';DROP",asset_ids=[4,4])
        self.assertEqual(id,1)
        self.assertEqual(db.calls[0][0],'begin');self.assertEqual(db.calls[-1][0],'commit')
        self.assertEqual(db.calls[1][1].params,(7337741004,))
        self.assertEqual(db.calls[4][1].params,(20,10,30))
        self.assertIn('t.exploration_id=n.exploration_id',db.calls[4][1].sql)
        self.assertEqual(db.calls[5][1].params[-1],json.dumps([4,4]))
        self.assertNotIn("';DROP",db.calls[5][1].sql)

    def test_create_cross_scope_and_archived_refused(self):
        db=Fake();store=FindingStore(db,task_id=10)
        with self.assertRaises(FindingInvalid):store.create(task_id=11,severity='high')
        self.assertEqual(db.calls,[])
        db=Fake([(None,),[(None,)],[('queued',)]])
        with self.assertRaises(FindingArchiveState):FindingStore(db).create(task_id=10,severity='high')
        self.assertEqual(db.calls[-1][0],'rollback')
        db=Fake([(None,),[(None,)],[],[]])
        with self.assertRaises(FindingInvalid):FindingStore(db).create(task_id=10,node_id=99,severity='high')
        self.assertEqual(db.calls[-1][0],'rollback')

    def test_status_validated_and_scoped_no_notifications(self):
        db=Fake([1]);store=FindingStore(db,task_id=10)
        self.assertEqual(store.set_status(1,'confirmed'),1)
        self.assertEqual(db.calls[0][1].params,('confirmed',1,10))
        self.assertIn('f.task_id=$3',db.calls[0][1].sql)
        with self.assertRaises(FindingInvalid):store.set_status(1,'arbitrary')
        self.assertEqual(len(db.calls),1)

    def test_metadata_atomic_mirror_and_failure_rollback(self):
        db=Fake([[(20,10)],1]);store=FindingStore(db,task_id=10)
        self.assertEqual(store.set_name(1,'new'),1)
        self.assertEqual(db.calls[1][1].params,('new',1,10))
        self.assertEqual(db.calls[2][1].params,('new',20,10))
        self.assertIn('content_version=content_version+1',db.calls[2][1].sql)
        self.assertIn('t.exploration_id=n.exploration_id',db.calls[2][1].sql)
        self.assertEqual(db.calls[-1][0],'commit')
        db=Fake([[(20,10)],RuntimeError('mirror failed')])
        with self.assertRaisesRegex(RuntimeError,'mirror failed'):FindingStore(db).set_summary(1,'new')
        self.assertEqual(db.calls[-1][0],'rollback')
        self.assertEqual(FindingStore(Fake([[]])).set_vulnclass(1,'class'),0)

    def test_report_lock_order_version_and_legacy_stale(self):
        db=Fake([(None,),[(1,10)],[(None,)],[],(3,),1])
        self.assertEqual(FindingStore(db,task_id=10).set_report_by_node_id(20,'Report',version=3),1)
        self.assertEqual(db.calls[1][1].params,(7337741004,))
        self.assertEqual(db.calls[2][1].params,(20,10))
        self.assertIn('FOR UPDATE',db.calls[3][1].sql)
        self.assertIn('task_archives',db.calls[4][1].sql)
        self.assertIn('evidence_version FROM findings',db.calls[5][1].sql)
        self.assertEqual(db.calls[6][1].params,(1,'Report',3))
        self.assertIn('CASE WHEN evidence_version=0 THEN 0 ELSE -1 END',db.calls[6][1].sql)
        self.assertEqual(db.calls[-1][0],'commit')
        db=Fake([(None,),[(1,None)],(3,),1])
        FindingStore(db).set_report_by_node_id(20,'Legacy report')
        self.assertEqual(db.calls[-2][1].params,(1,'Legacy report',None))

    def test_report_conflict_no_write_and_missing(self):
        db=Fake([(None,),[(1,None)],(4,)])
        with self.assertRaises(EvidenceConflict):FindingStore(db).set_report_by_node_id(20,'stale',version=3)
        self.assertFalse(any(op=='execute' for op,q in db.calls))
        self.assertEqual(db.calls[-1][0],'rollback')
        self.assertEqual(FindingStore(Fake([(None,),[]])).set_report_by_node_id(99,'report'),0)

    def test_unsupported_deletion_and_bad_inputs_no_query(self):
        db=Fake();store=FindingStore(db)
        for action in (lambda:store.create(severity='bad'),lambda:store.set_severity(1,'bad'),lambda:store.set_report_by_node_id(20,'x',-1)):
            with self.assertRaises(ValueError):action()
        with self.assertRaises(NotImplementedError):store.delete(1)
        self.assertEqual(db.calls,[])

if __name__=='__main__':unittest.main()
