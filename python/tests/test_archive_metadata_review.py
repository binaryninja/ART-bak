"""Independent bounded metadata and recording-schema checks; inert executors only."""
import unittest
from copy import deepcopy
from artex.archive_metadata import ArchiveOwnershipMetadata, MetadataUnavailable
from artex.db.recording_schema import ensure_recording_schema, SchemaRefused, USAGE_SQL
import test_archive_metadata as metadata_fixture
import test_recording_schema as schema_fixture

class ArchiveMetadataReview(unittest.TestCase):
    def test_oversized_protected_host_rejected(self):
        db=metadata_fixture.Executor();db.hosts=[('x'*2048,'','',False)];db.ids=[]
        with self.assertRaises(MetadataUnavailable):
            ArchiveOwnershipMetadata(max_json_bytes=1024)(db,1,2,{'assets':[]})
        self.assertEqual(db.aggregate_index,0)

    def test_read_failure_does_not_return_empty_ownership(self):
        db=metadata_fixture.Executor()
        def fail(query):raise RuntimeError('inert store failed')
        db.fetch_all=fail
        with self.assertRaises(RuntimeError):ArchiveOwnershipMetadata()(db,1,2,{'assets':[]})
        self.assertEqual(db.aggregate_index,0)

    def test_extra_recording_column_refuses_before_table_ddl(self):
        row=list(deepcopy(schema_fixture.catalog('llm_usage')))
        row[3].append(dict(name='unexpected',type='text',required=False,default=None,identity='',generated=''))
        db=schema_fixture.Fake([tuple(row),schema_fixture.ABSENT])
        with self.assertRaises(SchemaRefused):
            ensure_recording_schema(db,expected_database='fixture',action='create_or_upgrade')
        self.assertNotIn(USAGE_SQL,db.calls)
        self.assertEqual(db.events,['begin','rollback'])
