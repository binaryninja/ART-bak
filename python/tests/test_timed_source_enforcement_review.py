"""Independent pristine-plus-required-extension enforcement composition tests."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from artex.db import timed_source_schema as m
from test_timed_source_schema import descriptor, DB

class SourceEnforcementReviewTests(unittest.TestCase):
    def observe(self,body):return m.validate_timed_source_schema(DB(body),expected_database='fixture')
    def reject(self,body):
        with self.assertRaises(m.TimedSourceSchemaRefused):self.observe(body)
    def test_exact_required_extension_parent_footprint(self):
        body=descriptor();self.observe(body)
        rows=[r for r in body['constraint_triggers'] if r['source_table'].startswith('artex_')]
        expected=set()
        for table in ('artex_task_bootstrap','artex_final_assessment','artex_final_settlement','artex_early_completion','artex_unstamped_completion'):
            for target,column in (('tasks','task_id'),('explorations','exploration_id')):
                for fn,kind in (('RI_FKey_noaction_del',9),('RI_FKey_noaction_upd',17)):
                    expected.add((table,target,column,fn,kind))
        self.assertEqual(len(rows),20)
        self.assertEqual({(r['source_table'],r['table_name'],r['source_columns'][0],r['function_name'],r['type']) for r in rows},expected)
        self.assertEqual(len(body['constraint_triggers']),154)
    def test_pristine_manifest_alone_not_extended_runtime_contract(self):
        body=descriptor()
        pristine=json.loads(Path(m.__file__).with_name('timed_source_manifest.json').read_text())['contract']['constraint_triggers']
        self.assertEqual(len(pristine),134)
        body['constraint_triggers']=deepcopy(pristine);body['constraint_triggers_count']=len(pristine)
        self.reject(body)
    def test_every_captured_or_required_trigger_disabled_or_missing_refuses(self):
        clean=descriptor()
        for n,row in enumerate(clean['constraint_triggers']):
            with self.subTest(index=n,function=row['function_name']):
                body=deepcopy(clean);body['constraint_triggers'][n]['enabled']='D';self.reject(body)
                body=deepcopy(clean);body['constraint_triggers'].pop(n);body['constraint_triggers_count']-=1;self.reject(body)
    def test_replica_and_incoming_extension_or_source_footprint_drift_refuses(self):
        for role in ('replica','local',None,True):
            body=descriptor();body['replication_role']=role;self.reject(body)
        for target in ('tasks','artex_final_assessment'):
            body=descriptor();row=deepcopy(body['constraint_triggers'][0]);row.update(source_table='unexpected_child',table_name=target)
            body['constraint_triggers'].append(row);body['constraint_triggers_count']+=1;self.reject(body)
    def test_original_scalar_flags_not_normalized_during_composition(self):
        for field,value in (('deferrable',0),('deferred',0),('type',True),('source_columns',['wrong'])):
            body=descriptor();index=next(i for i,r in enumerate(body['constraint_triggers']) if r['source_table'].startswith('artex_'))
            body['constraint_triggers'][index][field]=value;self.reject(body)
