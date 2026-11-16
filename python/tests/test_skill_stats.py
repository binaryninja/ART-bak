import unittest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from artex.db.skill_stats import SkillStat as S, ArchivedSkillStats as A, merge_skill_stats as merge


class SkillStatsTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(merge([], []), [])

    def test_source_merge_order_and_latest(self):
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        new = old + timedelta(days=1)
        result = merge([S('a', 2, 1, ('worker',), old)], [A(skill_stats=(S('a', 3, 2, ('worker', '', 'planner'), new), S('b', 5)))])
        self.assertEqual(result, [S('a', 5, 3, ('worker', 'planner'), new), S('b', 5)])

    def test_legacy_fallback_only_without_new_stats(self):
        self.assertEqual(merge([], [A(skills={'x': 3})]), [S('x', 3, 1)])
        self.assertEqual(merge([], [A(skills={'x': 90}, skill_stats=(S('y', 1),))]), [S('y', 1)])

    def test_missing_never_invents_legacy_misses(self):
        self.assertEqual(merge([], [A(skills={'x': 3})], missing=True), [])
        self.assertEqual(merge([], [A(missing_skill_stats=(S('../not-a-path', 2),))], missing=True), [S('../not-a-path', 2)])

    def test_live_assignment_matches_source(self):
        self.assertEqual(merge([S('a', 1), S('a', 2)], []), [S('a', 2)])

    def test_inputs_preserved(self):
        live = [S('a', 1, agents=('x',))]
        archive = A(skill_stats=(S('a', 1, agents=('y',)),))
        self.assertEqual(merge(live, [archive])[0].agents, ('x', 'y'))
        self.assertEqual(live[0].agents, ('x',))
        self.assertEqual(archive.skill_stats[0].agents, ('y',))

    def test_invalid_counts(self):
        for value in (-1, True, 1.0, 2**63):
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge([S('x', value)], [])
        with self.assertRaises(ValueError):
            merge([S('x', 2**63-1)], [A(skill_stats=(S('x', 1),))])

    def test_bad_timestamp_and_container(self):
        with self.assertRaises(ValueError): merge([S('a', last_used=datetime(2026,1,1))], [])
        with self.assertRaises(ValueError): merge(iter([]), [])
        with self.assertRaises(ValueError): merge([S('a', agents=['x'])], [])

    def test_bounds_count_duplicates_and_utf8(self):
        with self.assertRaises(ValueError): merge([S('x'), S('x')], [], max_rows=1)
        with self.assertRaises(ValueError): merge([S('é')], [], max_text_bytes=1)
        with self.assertRaises(ValueError): merge([], [A(), A()], max_rows=1)
        with self.assertRaises(ValueError): merge([], [A(skills={'x': 1, 'y': 1})], max_rows=1)

    def test_aware_offset_comparison(self):
        a = datetime(2026,1,1,12,tzinfo=timezone.utc)
        b = datetime(2026,1,1,8,tzinfo=timezone(timedelta(hours=-5)))
        self.assertEqual(merge([S('a', last_used=a)], [A(skill_stats=(S('a',last_used=b),))])[0].last_used,b)

    def test_dst_fold_compares_instants_not_wall_time(self):
        zone = ZoneInfo('America/Toronto')
        earlier = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
        later = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1)
        result = merge([S('a', last_used=earlier)], [A(skill_stats=(S('a', last_used=later),))])
        self.assertIs(result[0].last_used, later)
        reverse = merge([S('a', last_used=later)], [A(skill_stats=(S('a', last_used=earlier),))])
        self.assertIs(reverse[0].last_used, later)

    def test_direct_timestamps_must_fit_utc_range(self):
        values = (datetime(1,1,1,tzinfo=timezone(timedelta(hours=23))),
                  datetime(9999,12,31,23,59,59,tzinfo=timezone(timedelta(hours=-23))))
        for stamp in values:
            row = S('a', last_used=stamp)
            with self.subTest(stamp=stamp,source='live'), self.assertRaisesRegex(ValueError, 'outside UTC range'):
                merge([row], [])
            with self.subTest(stamp=stamp,source='cold'), self.assertRaisesRegex(ValueError, 'outside UTC range'):
                merge([], [A(skill_stats=(row,))])
            with self.subTest(stamp=stamp,source='missing'), self.assertRaisesRegex(ValueError, 'outside UTC range'):
                merge([], [A(missing_skill_stats=(row,))], missing=True)
        for stamp in (datetime.min.replace(tzinfo=timezone.utc), datetime.max.replace(tzinfo=timezone.utc)):
            row = S('a', last_used=stamp)
            self.assertIs(merge([row], [])[0].last_used, stamp)
            self.assertIs(merge([], [A(skill_stats=(row,))])[0].last_used, stamp)
            self.assertIs(merge([], [A(missing_skill_stats=(row,))], missing=True)[0].last_used, stamp)

if __name__ == '__main__': unittest.main()
