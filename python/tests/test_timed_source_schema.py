"""Read-only descriptor tests; no source schema or database is executed here."""
from copy import deepcopy
import hashlib,json
from pathlib import Path
import unittest
from artex.db import timed_source_schema as m


def descriptor():
    manifest=json.loads(Path(m.__file__).with_name('timed_source_manifest.json').read_text())
    result=deepcopy(manifest['contract'])
    result['constraint_triggers'] += [row for row in m._expected_fk_triggers() if row['table_name'] in ('tasks','explorations')]
    result.update(database='fixture',schema='public',version=170011,temp_shadow_count=0,replication_role='origin',
                  effective_schemas=['pg_catalog','public'],search_path='"$user", public')
    for key in m._LIMITS:result[key+'_count']=len(result[key])
    return result

class DB:
    def __init__(self,body):self.body=body;self.calls=[]
    def fetch_all(self,query):self.calls.append(query);return [(self.body,)]

class Tests(unittest.TestCase):
    def test_reviewed_manifest_positive_one_snapshot(self):
        db=DB(descriptor());result=m.validate_timed_source_schema(db,expected_database='fixture')
        self.assertEqual(len(db.calls),1);self.assertEqual(result.tables,m._TABLES)
        self.assertFalse(result.source_readiness);self.assertEqual(result.manifest_digest,m._BASELINE_SHA)
        self.assertEqual(db.calls[0].params,('public',list(m._NAMES),list(m._TABLES),m._MAX_BYTES,list(m._FUNCTIONS)))
    def reject(self,edit):
        b=descriptor();edit(b)
        with self.assertRaises(m.TimedSourceSchemaRefused):m.validate_timed_source_schema(DB(b),expected_database='fixture')
    def test_every_semantic_row_is_compared_without_coercion(self):
        for group in m._LIMITS:
            with self.subTest(group=group):self.reject(lambda b,g=group:b[g][0].update(unrecognized=True))
        self.reject(lambda b:b['columns'][0].update(position=True))
        self.reject(lambda b:b['columns'][0].update(not_null=1))
        self.reject(lambda b:b['indexes'][0].update(valid=1))
    def test_complete_known_extension_composition_required(self):
        b=descriptor()
        added=[row for row in b['constraint_triggers'] if row['source_table'].startswith('artex_')]
        self.assertEqual(len(added),20)
        self.assertEqual(len(b['constraint_triggers']),154)
        for remove in (1,20):
            copy=deepcopy(b);copy['constraint_triggers']=copy['constraint_triggers'][:-remove]
            copy['constraint_triggers_count']=len(copy['constraint_triggers'])
            with self.assertRaises(m.TimedSourceSchemaRefused):m.validate_timed_source_schema(DB(copy),expected_database='fixture')
        self.reject(lambda b:b['constraint_triggers'][-1].update(enabled='D'))

    def test_defaults_keys_triggers_and_function_bodies_are_exact(self):
        self.reject(lambda b:b['columns'][0].update(default_expr='private_function()'))
        self.reject(lambda b:b['constraints'][0].update(deferrable=True))
        self.reject(lambda b:b['triggers'][0].update(enabled='D'))
        self.reject(lambda b:b['functions'][0].update(body='BEGIN RETURN NULL; END;'))
        self.reject(lambda b:b['functions'][0].update(security_definer=True))
        self.reject(lambda b:b['sequences'][0].update(increment=2))
        self.reject(lambda b:b['constraint_triggers'][0].update(enabled='D'))
        self.reject(lambda b:b['constraint_triggers'][0].update(function_name='unexpected'))
        self.reject(lambda b:b.update(replication_role='replica'))
        self.reject(lambda b:b['database_locale'].update(collate='different'))
        self.reject(lambda b:b['columns'][0].update(collation=['public','unexpected']))
    def test_scope_and_effective_resolution_are_not_guessed(self):
        for edit in (lambda b:b.update(version=180000),lambda b:b.update(database='other'),
                     lambda b:b.update(temp_shadow_count=True),lambda b:b.update(temp_shadow_count=1),
                     lambda b:b.update(effective_schemas=['pg_catalog','other','public']),
                     lambda b:b['relations'][0].update(resolves_exact=False)):
            self.reject(edit)
    def test_missing_extra_oversize_and_non_json_rejected(self):
        self.reject(lambda b:b['columns'].pop())
        self.reject(lambda b:b['columns'].append(b['columns'][0]))
        self.reject(lambda b:b.update(columns_count=True))
        self.reject(lambda b:b.update(columns_count=513))
        self.reject(lambda b:b['functions'][0].update(body='X'*(m._MAX_BYTES+1)))
        self.reject(lambda b:b['columns'][0].update(position=1.0))
    def test_order_independence_not_numeric_equivalence(self):
        a=descriptor();b=deepcopy(a)
        for key in m._LIMITS:b[key].reverse()
        left=m.validate_timed_source_schema(DB(a),expected_database='fixture')
        right=m.validate_timed_source_schema(DB(b),expected_database='fixture')
        self.assertEqual(left.descriptor_digest,right.descriptor_digest)
    def test_invalid_request_before_sql_and_errors_redacted(self):
        db=DB(None)
        for kwargs in ({'expected_database':''},{'expected_database':'fixture','profile':'all'},
                       {'expected_database':'fixture','expected_schema':'other'}):
            with self.assertRaises(ValueError):m.validate_timed_source_schema(db,**kwargs)
        self.assertEqual(db.calls,[])
        class Broken:
            def fetch_all(self,q):raise RuntimeError('private credential marker')
        with self.assertRaises(m.TimedSourceSchemaRefused) as e:m.validate_timed_source_schema(Broken(),expected_database='fixture')
        self.assertNotIn('credential',str(e.exception))
    def test_sql_is_catalog_only_bounded_no_executed_expression(self):
        sql=m.SNAPSHOT_SQL
        self.assertTrue(sql.startswith('WITH relations'))
        for text in ('SET ', 'INSERT INTO ', 'UPDATE ', 'DELETE FROM ', 'nextval(', 'pg_advisory'):
            self.assertNotIn(text,sql)
        for text in ('pg_my_temp_schema','current_schemas(true)','LIMIT 512','LIMIT 256','pg_get_triggerdef','pg_get_functiondef','octet_length(body::text)'):
            self.assertIn(text,sql)
