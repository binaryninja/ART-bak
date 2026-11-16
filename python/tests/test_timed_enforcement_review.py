"""Independent internal FK enforcement comparison. Catalog doubles only.

Actual missing-query evidence is separately retained in isolated PG QA.
"""
from copy import deepcopy
import unittest
from artex.db import timed_schema as m
from test_timed_schema import descriptor, DB

class EnforcementReviewTests(unittest.TestCase):
    def reject(self,body):
        with self.assertRaises(m.TimedSchemaRefused):
            m.validate_timed_schema(DB(body),expected_database='fixture')
    def test_every_child_and_parent_trigger_is_required_and_enabled(self):
        clean=descriptor()
        self.assertEqual(len(clean['constraint_triggers']),44)
        for n,row in enumerate(clean['constraint_triggers']):
            with self.subTest(index=n,function=row['function_name']):
                body=deepcopy(clean);body['constraint_triggers'][n]['enabled']='D';self.reject(body)
                body=deepcopy(clean);body['constraint_triggers'].pop(n);body['constraint_triggers_count']-=1;self.reject(body)
    def test_replica_local_missing_and_wrong_type_role_refused(self):
        for role in ('replica','local',None,True,1):
            body=descriptor();body['replication_role']=role;self.reject(body)
        body=descriptor();del body['replication_role'];self.reject(body)
    def test_all_trigger_semantics_and_scalar_types_are_exact(self):
        changes={'deferrable':0,'deferred':0,'type':True,'condition':'true','args':'00',
                 'function_schema':'public','function_name':'RI_FKey_check_ins_changed',
                 'source_columns':['other'],'target_columns':['other'],'source_schema':'other',
                 'constraint_kind':'p','update_action':'c','delete_action':'c','match_type':'f'}
        for field,value in changes.items():
            with self.subTest(field=field):
                body=descriptor();body['constraint_triggers'][0][field]=value;self.reject(body)
    def test_extra_duplicate_and_overflow_trigger_footprint_refused(self):
        body=descriptor();body['constraint_triggers'].append(deepcopy(body['constraint_triggers'][0]));body['constraint_triggers_count']+=1;self.reject(body)
        body=descriptor();body['constraint_triggers_count']=True;self.reject(body)
        body=descriptor();body['constraint_triggers']*=12;body['constraint_triggers_count']=len(body['constraint_triggers']);self.reject(body)
    def test_order_is_irrelevant_but_generated_names_not_part_of_contract(self):
        body=descriptor();a=m.validate_timed_schema(DB(body),expected_database='fixture')
        body['constraint_triggers'].reverse();b=m.validate_timed_schema(DB(body),expected_database='fixture')
        self.assertEqual(a,b)
        for row in body['constraint_triggers']:
            self.assertNotIn('oid',row);self.assertNotIn('name',row)
        projection=m.SNAPSHOT_SQL.split('constraint_trigger_rows AS (',1)[1].split('), snapshot AS',1)[0]
        self.assertNotIn('t.tgname',projection)
        self.assertIn('t.tgisinternal',projection)
