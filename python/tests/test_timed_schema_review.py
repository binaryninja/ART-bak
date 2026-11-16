"""Independent descriptor validation; no database/schema execution."""
from copy import deepcopy
import unittest
from artex.db import timed_schema as m
from test_timed_schema import descriptor,DB

class SchemaReview(unittest.TestCase):
    def reject(self,edit):
        data=descriptor();edit(data)
        with self.assertRaises(m.TimedSchemaRefused):m.validate_timed_schema(DB(data),expected_database='fixture')
    def test_boolean_numeric_shapes_do_not_match_integer_contracts(self):
        edits=(lambda d:d['columns'][0].update(position=True),
               lambda d:d['relations'][0].update(inheritance=False),
               lambda d:d['sequences'][0].update(cache=True),
               lambda d:d['indexes'][0].update(key_count=True,total_count=True),
               lambda d:d['columns'][0].update(custom_dependencies=False))
        for edit in edits:
            with self.subTest(edit=edit):self.reject(edit)
    def test_duplicate_check_and_foreign_cascade_denied(self):
        def duplicate(d):
            d['constraints'].append(deepcopy(next(c for c in d['constraints'] if c['kind']=='c')))
            d['constraints_count']+=1
        self.reject(duplicate)
        self.reject(lambda d:next(c for c in d['constraints'] if c['kind']=='f').update(delete_action='c'))
    def test_custom_operator_and_sequence_reference_not_accepted(self):
        self.reject(lambda d:next(c for c in d['constraints'] if c['kind']=='c').update(custom_dependencies=1))
        self.reject(lambda d:d['columns'][2].update(sequence_refs=[['public',m._SEQUENCES[0]],['public','other']]))
    def test_valid_descriptor_digest_does_not_depend_on_catalog_set_order(self):
        one=descriptor();other=deepcopy(one)
        for key in ('relations','constraints','indexes','sequences'):other[key].reverse()
        a=m.validate_timed_schema(DB(one),expected_database='fixture')
        b=m.validate_timed_schema(DB(other),expected_database='fixture')
        self.assertEqual(a.descriptor_digest,b.descriptor_digest)
        self.assertFalse(a.source_readiness)
    def test_null_overflow_and_wrong_tuple_are_redacted(self):
        class Reader:
            def __init__(self,rows):self.rows=rows
            def fetch_all(self,q):return self.rows
        for rows in ([(None,)],[],[(descriptor(),),(descriptor(),)],[(descriptor(),'extra')]):
            with self.subTest(rows_type=type(rows)),self.assertRaises(m.TimedSchemaRefused):m.validate_timed_schema(Reader(rows),expected_database='fixture')
    def test_no_read_data_or_mutation_interfaces_needed(self):
        db=DB(descriptor())
        result=m.validate_timed_schema(db,expected_database='fixture')
        self.assertEqual(len(db.calls),1)
        self.assertEqual(db.calls[0].sql,m.SNAPSHOT_SQL)
        self.assertFalse(result.source_readiness)
