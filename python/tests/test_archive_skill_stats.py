import json
import unittest
from datetime import datetime, timezone
from artex.db.archive_skill_stats import decode_archive_skill_stats as decode
from artex.db.skill_stats import ArchivedSkillStats as A, SkillStat as S, merge_skill_stats


class ArchiveSkillStatsTests(unittest.TestCase):
    def test_empty_and_null_zero_values(self):
        for raw in ('{}', 'null', '{"skills":null,"skill_stats":null,"missing_skill_stats":null}'):
            self.assertEqual(decode(raw), A())

    def test_current_dimensions_and_ignored_others(self):
        raw = json.dumps({'token_daily':[{'date':'2026-01-01'}], 'skill_stats':[
            {'skill':'search','calls':3,'tasks':2,'agents':['worker'], 'last_used':'2026-01-01T01:02:03.123456Z'}],
            'missing_skill_stats':[{'skill':'../../wish', 'calls':1}]})
        result = decode(raw.encode())
        self.assertEqual(result.skill_stats, (S('search',3,2,('worker',),datetime(2026,1,1,1,2,3,123456,tzinfo=timezone.utc)),))
        self.assertEqual(result.missing_skill_stats, (S('../../wish',1),))

    def test_legacy_and_merge(self):
        projection = decode('{"skills":{"search":5}}')
        self.assertEqual(merge_skill_stats([], [projection]), [S('search',5,1)])
        self.assertEqual(merge_skill_stats([], [projection], missing=True), [])

    def test_duplicate_keys_anywhere_refused(self):
        for raw in ('{"skills":{},"skills":{}}', '{"ignored":{"x":1,"x":2}}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): decode(raw)

    def test_bad_counts(self):
        for value in (-1, True, 1.5, 2**63, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode(json.dumps({'skill_stats':[{'skill':'s', 'calls':value}]}))
            with self.subTest(legacy=value), self.assertRaises(ValueError):
                decode(json.dumps({'skills':{'s':value}}))

    def test_bad_shapes(self):
        for raw in ('[]', '1', '{"skills":[]}', '{"skill_stats":{}}', '{"skill_stats":[null]}',
                    '{"skill_stats":[{}]}', '{"skill_stats":[{"skill":"s","agents":[1]}]}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): decode(raw)

    def test_bad_timestamps(self):
        for value in ('2026-01-01', '2026-01-01T12:00:00', '2026-01-01T12:00:00.1234567Z',
                      '2026-01-01T12:00:00+01:99', '2026-01-01T12:00:00+24:00',
                      '2026-02-30T12:00:00Z'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode(json.dumps({'skill_stats':[{'skill':'s','last_used':value}]}))

    def test_utc_offset_preserved(self):
        projection = decode('{"skill_stats":[{"skill":"s","last_used":"2026-01-01T12:00:00-05:00"}]}')
        self.assertEqual(projection.skill_stats[0].last_used.isoformat(),'2026-01-01T12:00:00-05:00')

    def test_utc_representability_checked_for_each_timestamp(self):
        for value in ('0001-01-01T00:00:00+23:00', '9999-12-31T23:59:59-23:00'):
            for key in ('skill_stats', 'missing_skill_stats'):
                with self.subTest(value=value,key=key), self.assertRaisesRegex(ValueError, 'outside UTC range'):
                    decode(json.dumps({key:[{'skill':'s','last_used':value}]}))
        for value in ('0001-01-01T00:00:00Z', '9999-12-31T23:59:59.999999Z'):
            self.assertIsNotNone(decode(json.dumps({'skill_stats':[{'skill':'s','last_used':value}]})).skill_stats[0].last_used)

    def test_size_depth_nodes_and_rows(self):
        with self.assertRaises(ValueError): decode('"é"', max_bytes=3)
        with self.assertRaises(ValueError): decode('{"unknown":[[]]}', max_depth=2)
        with self.assertRaises(ValueError): decode('{"x":[1,2]}', max_nodes=3)
        with self.assertRaises(ValueError): decode('{"skills":{"a":1},"skill_stats":[{"skill":"b"}]}',max_rows=1)
        self.assertEqual(decode('{"unknown":"[[[\\\"[[["}',max_depth=1), A())

    def test_invalid_utf8_and_nonfinite(self):
        for raw in (b'\xff', '{"x":"\\ud800"}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): decode(raw)

    def test_limits_exact_types(self):
        for kwargs in ({'max_bytes':True},{'max_depth':0},{'max_nodes':1000001}):
            with self.assertRaises(ValueError): decode('{}',**kwargs)
        with self.assertRaises(ValueError): decode({})

if __name__ == '__main__': unittest.main()
