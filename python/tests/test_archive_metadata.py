import json,unittest
from pathlib import Path
from artex.archive_metadata import ArchiveOwnershipMetadata,MetadataUnavailable,HOSTS_SQL,EXCLUSIVE_SQL,TOKENS_SQL,AGGREGATES
from artex.archive_snapshot import ArchiveSnapshotExporter
from test_archive_snapshot import Executor as SnapshotExecutor
from contextlib import contextmanager

class Executor:
    def __init__(self):self.calls=[];self.hosts=[('EXAMPLE.COM','','',True),('shared.test','','',True),('SHARED.test','','',False),('','192.0.2.1','https://[2001:db8::1]:443/x',True)];self.ids=[(1,)];self.aggregate_index=0
    def fetch_all(self,q):self.calls.append(q);return self.hosts if 'archive_hosts(domain' in q.sql else self.ids
    def fetch_one(self,q):
        self.calls.append(q)
        if q.sql==TOKENS_SQL:return (4,10,20,3,2)
        name=AGGREGATES[self.aggregate_index][0];self.aggregate_index+=1
        value={} if name in ('skills','tools','findings','finding_stats') else []
        if name=='tools':value={'read':5}
        return (json.dumps(value),)

class MetadataTests(unittest.TestCase):
    def setUp(self):self.db=Executor();self.tables={'assets':[{'id':1,'domain':' EXAMPLE.com ','ip':'192.0.2.1','url':'https://[2001:db8::1]:443/x'},{'id':2,'domain':'shared.test'}]}
    def test_all_source_sql_preserved(self):
        root=Path(__file__).resolve().parents[2];source=(root/'db/task_archives.go').read_text();assets=(root/'db/assets.go').read_text()
        self.assertIn('`'+HOSTS_SQL+'`',assets);self.assertIn('`'+TOKENS_SQL+'`',source)
        for _,sql,_ in AGGREGATES:self.assertIn('`'+sql+'`',source)
        self.assertIn('asset.company_id IS NULL',EXCLUSIVE_SQL);self.assertIn('task.deleted_at IS NULL',EXCLUSIVE_SQL);self.assertIn('task_asset_links',EXCLUSIVE_SQL)
    def test_shared_hosts_assets_and_real_counts(self):
        result=ArchiveOwnershipMetadata()(self.db,7,8,self.tables)
        self.assertEqual(result['hosts'],['192.0.2.1','2001:db8::1','example.com','shared.test'])
        self.assertEqual(result['exclusive_hosts'],['192.0.2.1','2001:db8::1','example.com']);self.assertEqual(result['exclusive_asset_ids'],[1])
        self.assertEqual(result['aggregate_stats']['tokens']['calls'],4);self.assertEqual(result['aggregate_stats']['tools'],{'read':5})
        self.assertEqual(self.db.calls[0].params,(7,8,10001,8<<20));self.assertEqual(self.db.calls[2].params,('7',8))
        for q in self.db.calls[3:]:self.assertIn('octet_length',q.sql)
    def test_exclusive_outside_snapshot_rejected(self):
        self.db.ids=[(999,)]
        with self.assertRaises(MetadataUnavailable):ArchiveOwnershipMetadata()(self.db,7,8,self.tables)
    def test_ambiguous_url_failclosed(self):
        self.tables['assets'][0]['url']='https://bad%host/x'
        with self.assertRaises(MetadataUnavailable):ArchiveOwnershipMetadata()(self.db,7,8,self.tables)
        self.assertEqual(self.db.calls,[])
    def test_row_bound_never_truncates(self):
        with self.assertRaises(MetadataUnavailable):ArchiveOwnershipMetadata(max_rows=2)(self.db,7,8,self.tables)
    def test_bad_aggregate_never_zero_substitution(self):
        original=self.db.fetch_one
        self.db.fetch_one=lambda q:(None,) if q.sql!=TOKENS_SQL else original(q)
        with self.assertRaises(MetadataUnavailable):ArchiveOwnershipMetadata()(self.db,7,8,self.tables)
    def test_integrates_actual_exporter_inside_transaction(self):
        class Database(SnapshotExecutor):
            def __init__(self):super().__init__();self.meta=Executor();self.meta.hosts=[];self.meta.ids=[]
            def fetch_all(self,q):
                if 'archive_hosts(domain' in q.sql or 'archive_exclusive LIMIT' in q.sql:return self.meta.fetch_all(q)
                return super().fetch_all(q)
            def fetch_one(self,q):
                if q.sql==TOKENS_SQL or 'aggregate_value(value)' in q.sql:return self.meta.fetch_one(q)
                return super().fetch_one(q)
        db=Database()
        @contextmanager
        def guard(task):yield
        result=ArchiveSnapshotExporter(db,quiescence_guard=guard,metadata=ArchiveOwnershipMetadata()).export(1)
        self.assertEqual(result.manifest['aggregate_stats']['tokens']['calls'],4)
        self.assertEqual(db.events,['begin','commit']);self.assertEqual(len(db.meta.calls),11)
