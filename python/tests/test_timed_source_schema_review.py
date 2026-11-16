"""Independent closed-manifest review; inert catalog responses, no SQL execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from artex.db import timed_source_schema as m
from test_timed_source_schema import DB, descriptor


class ReviewTests(unittest.TestCase):
    def observe(self, body):
        return m.validate_timed_source_schema(DB(body), expected_database='fixture')

    def test_manifest_is_code_pinned_original_schema_not_target_capture(self):
        raw=Path(m.__file__).with_name('timed_source_manifest.json').read_bytes()
        manifest=json.loads(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),m._BASELINE_SHA)
        root=Path(__file__).resolve().parents[2]
        for source in (root/'db/schema.sql',root/'python/artex/db/schema.sql'):
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),m._SOURCE_SHA256)
        self.assertEqual(manifest['provenance']['source_sha256'],m._SOURCE_SHA256)
        self.assertEqual(type(manifest['provenance']['server_major']),int)
        self.assertEqual(manifest['provenance']['server_major'],17)
        self.assertFalse(self.observe(descriptor()).source_readiness)

    def test_missing_or_modified_manifest_never_queries_target(self):
        body=descriptor()
        for value in (b'{}', b'x'*(m._MAX_BYTES+1)):
            db=DB(body)
            with patch.object(Path,'read_bytes',return_value=value):
                with self.assertRaises(m.TimedSourceSchemaRefused):
                    m.validate_timed_source_schema(db,expected_database='fixture')
            self.assertEqual(db.calls,[])
        db=DB(body)
        with patch.object(Path,'read_bytes',side_effect=FileNotFoundError('private marker')):
            with self.assertRaises(m.TimedSourceSchemaRefused) as raised:
                m.validate_timed_source_schema(db,expected_database='fixture')
        self.assertEqual(db.calls,[])
        self.assertNotIn('private marker',str(raised.exception))

    def test_nested_type_and_dependency_drift_never_coerce(self):
        mutations=(
            ('relations','inheritance',True),
            ('relations','rules',1),
            ('relations','policies',1),
            ('relations','dropped_columns',1),
            ('columns','custom_dependencies',1),
            ('columns','sequence_refs',[['other','tasks_id_seq']]),
            ('indexes','opclasses',[['public','custom_ops']]),
            ('functions','extension_dependencies',1),
            ('functions','config',['search_path=private, public']),
            ('sequences','owners',[['private','tasks','id','a']]),
        )
        for group,key,value in mutations:
            with self.subTest(group=group,key=key):
                body=descriptor();body[group][0][key]=value
                with self.assertRaises(m.TimedSourceSchemaRefused):self.observe(body)

    def test_extra_semantic_rows_and_every_count_bool_rejected(self):
        for group in m._LIMITS:
            with self.subTest(group=group):
                body=descriptor();body[group].append(deepcopy(body[group][0]));body[group+'_count']+=1
                with self.assertRaises(m.TimedSourceSchemaRefused):self.observe(body)
                body=descriptor();body[group+'_count']=True
                with self.assertRaises(m.TimedSourceSchemaRefused):self.observe(body)

    def test_result_shape_depth_and_non_json_are_bounded(self):
        class Rows:
            def __init__(self,value):self.value=value
            def fetch_all(self,query):return self.value
        for result in ([],[(descriptor(),),(descriptor(),)],[(descriptor(),0)],{'body':descriptor()}):
            with self.subTest(shape=type(result)):
                with self.assertRaises(m.TimedSourceSchemaRefused):
                    m.validate_timed_source_schema(Rows(result),expected_database='fixture')
        for value in (float('nan'),object(),b'bytes'):
            body=descriptor();body['functions'][0]['body']=value
            with self.assertRaises(m.TimedSourceSchemaRefused):self.observe(body)
        body=descriptor();nested=None
        for _ in range(30):nested=[nested]
        body['functions'][0]['body']=nested
        with self.assertRaises(m.TimedSourceSchemaRefused):self.observe(body)

    def test_observation_does_not_learn_modified_target(self):
        clean=descriptor();changed=deepcopy(clean)
        changed['functions'][0]['body']+='\n-- changed target'
        with self.assertRaises(m.TimedSourceSchemaRefused):self.observe(changed)
        first=self.observe(clean);second=self.observe(clean)
        self.assertEqual(first,second)
