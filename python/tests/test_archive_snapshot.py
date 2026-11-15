import json,unittest
from contextlib import contextmanager
from pathlib import Path
from artex.archive_snapshot import ArchiveSnapshotExporter,QUERIES,SnapshotUnavailable,SnapshotLimit
from artex.archive_bundle import build_bundle,read_bundle

class Executor:
    def __init__(self):self.calls=[];self.events=[];self.index=0;self.bad=False
    @contextmanager
    def archive_snapshot_transaction(self):
        self.events.append('begin')
        try:yield self
        except BaseException:self.events.append('rollback');raise
        else:self.events.append('commit')
    def execute(self,q):self.calls.append(q);return 1
    def fetch_one(self,q):self.calls.append(q);return (2,)
    def fetch_all(self,q):
        self.calls.append(q);name=QUERIES[self.index][0];self.index+=1
        if self.bad:return [(None,100000)]
        values={'tasks':[{'id':1}],'explorations':[{'id':2}],'task_relations':[{'task_id':1,'source_task_id':8}],'llm_records':[{'id':9,'request':'fixture'}]}.get(name,[])
        return [(raw,len(raw.encode())) for raw in map(json.dumps,values)]

class SnapshotTests(unittest.TestCase):
    def setUp(self):self.db=Executor();self.events=[]
    @contextmanager
    def guard(self,id):
        self.events.append(('enter',id))
        try:yield
        finally:self.events.append(('exit',list(self.db.events)))
    def metadata(self,tx,task,exp,tables):
        self.assertIs(tx,self.db);self.assertEqual((task,exp),(1,2));self.assertEqual(len(tables),22)
        return dict(hosts=[],exclusive_hosts=[],exclusive_asset_ids=[],aggregate_stats={'fixture':True})
    def exporter(self,**kw):return ArchiveSnapshotExporter(self.db,quiescence_guard=self.guard,metadata=self.metadata,**kw)
    def test_source_queries_and_scope(self):
        source=(Path(__file__).resolve().parents[2]/'db/task_archives.go').read_text()
        self.assertEqual(len(QUERIES),22)
        for name,sql,args in QUERIES:
            if name!='assets':self.assertIn('`'+sql+'`',source)
            else:self.assertIn("value ~ '^[0-9]+$'",sql)
            self.assertTrue(args)
    def test_real_snapshot_to_bundle_contract(self):
        output=self.exporter().export(1)
        self.assertEqual(output.manifest['source_task_ids'],[8]);self.assertEqual(output.manifest['tables']['llm_records'],[])
        self.assertEqual(output.manifest['data_counts']['llm_records'],1)
        result=read_bundle(build_bundle(output.manifest,output.files));self.assertEqual(result.manifest['task_id'],1)
        self.assertEqual(self.db.calls[0].params,(7337741001,));self.assertEqual(self.events[-1],('exit',['begin','commit']))
        for q in self.db.calls[2:]:self.assertIn('LIMIT $',q.sql)
    def test_missing_capabilities_before_sql(self):
        with self.assertRaises(SnapshotUnavailable):ArchiveSnapshotExporter(self.db).export(1)
        self.assertEqual(self.db.calls,[])
    def test_byte_bound_rolls_back(self):
        self.db.bad=True
        with self.assertRaises(SnapshotLimit):self.exporter(max_bytes=1000).export(1)
        self.assertEqual(self.db.events,['begin','rollback'])
    def test_incomplete_metadata_not_faked(self):
        exporter=self.exporter();exporter.metadata=lambda *args:{}
        with self.assertRaises(SnapshotUnavailable):exporter.export(1)
        self.assertEqual(self.db.events[-1],'rollback')
    def test_no_live_parent(self):
        self.db.fetch_one=lambda q:None
        with self.assertRaises(SnapshotUnavailable):self.exporter().export(1)
        self.assertEqual(self.db.index,0)
    def test_invalid_scope_no_read(self):
        for value in (0,-1,True,'1'):
            with self.assertRaises(ValueError):self.exporter().export(value)
        self.assertEqual(self.db.calls,[])

    def test_real_no_rows_error_typed(self):
        from artex.db.postgres import NoRowsError
        def missing(query):
            if 'FROM tasks' in query.sql:raise NoRowsError()
            return (None,)
        self.db.fetch_one=missing
        with self.assertRaises(SnapshotUnavailable):self.exporter().export(1)
        self.assertEqual(self.db.events[-1],'rollback')
