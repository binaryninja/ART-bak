from contextlib import contextmanager
from datetime import datetime, timezone
import unittest
from artex.db.task_categories import (TaskCategoryStore, CategoryInvalid,
    CategoryNameConflict, CategoryNotFound, CategoryTaskNotFound, normalize_name)

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
ROW = (7, 'A Name', 'a name', 2, NOW, NOW)


class Fake:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = []

    def _call(self, method, query):
        self.calls.append((method, query))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def fetch_all(self, query):
        return self._call('fetch_all', query)

    def fetch_one(self, query):
        return self._call('fetch_one', query)

    def execute(self, query):
        return self._call('execute', query)

    @contextmanager
    def transaction(self):
        self.calls.append(('begin', None))
        try:
            yield self
        except BaseException:
            self.calls.append(('rollback', None))
            raise
        else:
            self.calls.append(('commit', None))


class CategoriesTests(unittest.TestCase):
    def test_name_normalization(self):
        self.assertEqual(normalize_name(' \t A  Name\u2000 '), ('A Name', 'a name'))
        self.assertEqual(normalize_name('ΟΣ İ'), ('ΟΣ İ', 'οσ i'))
        self.assertEqual(normalize_name('\x1cA'), ('\x1cA', '\x1ca'))
        self.assertEqual(len(normalize_name('界' * 80)[0]), 80)
        for value in ('', ' \t', '界' * 81, None, 123):
            with self.assertRaises(CategoryInvalid):
                normalize_name(value)

    def test_create_bound_parameters_and_duplicate(self):
        db = Fake([[ROW], []])
        store = TaskCategoryStore(db)
        result = store.create('  A    Name ')
        self.assertEqual(result.id, 7)
        self.assertNotIn('nkey', result.to_dict())
        self.assertEqual(db.calls[0][1].params, ('A Name', 'a name'))
        self.assertIn('VALUES ($1,$2)', db.calls[0][1].sql)
        self.assertIn('ON CONFLICT (nkey) DO NOTHING', db.calls[0][1].sql)
        with self.assertRaises(CategoryNameConflict):
            store.create('A Name')
        malicious = "'); DELETE FROM tasks; --"
        db = Fake([[ROW]])
        TaskCategoryStore(db).create(malicious)
        self.assertNotIn(malicious, db.calls[0][1].sql)
        self.assertEqual(db.calls[0][1].params[0], malicious)

    def test_list_get_delete(self):
        db = Fake([[ROW], [], [ROW], 1, 0])
        store = TaskCategoryStore(db)
        self.assertEqual(store.list()[0].task_count, 2)
        self.assertIn('ORDER BY category.name, category.id', db.calls[0][1].sql)
        self.assertIn('FILTER (WHERE task.deleted_at IS NULL)', db.calls[0][1].sql)
        self.assertIsNone(store.get(99))
        self.assertEqual(store.get(7).name, 'A Name')
        self.assertTrue(store.delete(7))
        self.assertFalse(store.delete(7))
        self.assertEqual(db.calls[-1][1].sql, 'DELETE FROM task_categories WHERE id=$1')
        self.assertEqual(db.calls[-1][1].params, (7,))

    def test_rename_errors_and_parameters(self):
        db = Fake([[ROW], []])
        store = TaskCategoryStore(db)
        self.assertEqual(store.rename(7, ' A Name ').id, 7)
        self.assertEqual(db.calls[0][1].params, (7, 'A Name', 'a name'))
        with self.assertRaises(CategoryNotFound):
            store.rename(88, 'New')
        unique = RuntimeError('unique conflict')
        unique.sqlstate = '23505'
        with self.assertRaises(CategoryNameConflict):
            TaskCategoryStore(Fake([unique])).rename(7, 'Existing')
        error = RuntimeError('database unavailable')
        with self.assertRaises(RuntimeError) as raised:
            TaskCategoryStore(Fake([error])).rename(7, 'New')
        self.assertIs(raised.exception, error)

    def test_assignment_and_missing_rows(self):
        db = Fake([1, [ROW]])
        store = TaskCategoryStore(db)
        self.assertIsNone(store.set_task(4, None))
        self.assertEqual(store.set_task(4, 7).id, 7)
        self.assertEqual(db.calls[0][1].params, (4,))
        self.assertEqual(db.calls[1][1].params, (4, 7))
        self.assertIn('deleted_at IS NULL', db.calls[1][1].sql)
        with self.assertRaises(CategoryTaskNotFound):
            TaskCategoryStore(Fake([0])).set_task(4, None)
        for exists, expected in ((False, CategoryTaskNotFound), (True, CategoryNotFound)):
            with self.assertRaises(expected):
                TaskCategoryStore(Fake([[], (exists,)])).set_task(4, 7)

    def test_batch_transaction_order_and_refresh(self):
        db = Fake([[(7,)], [(3,), (1,)], ROW])
        updated, category = TaskCategoryStore(db).set_tasks([1, 3, 999], 7)
        self.assertEqual(updated, [3, 1])
        self.assertEqual(category.task_count, 2)
        self.assertEqual([method for method, _ in db.calls],
                         ['begin', 'fetch_all', 'fetch_all', 'fetch_one', 'commit'])
        self.assertEqual(db.calls[1][1].sql, 'SELECT id FROM task_categories WHERE id=$1 FOR KEY SHARE')
        self.assertEqual(db.calls[1][1].params, (7,))
        self.assertEqual(db.calls[2][1].params, ([1, 3, 999], 7))
        self.assertIn('id=ANY($1::bigint[]) AND deleted_at IS NULL', db.calls[2][1].sql)
        self.assertEqual(db.calls[3][1].params, (7,))

    def test_batch_clear_missing_and_rollback(self):
        db = Fake([[(1,)]])
        self.assertEqual(TaskCategoryStore(db).set_tasks([1, 2], None), ([1], None))
        self.assertEqual([method for method, _ in db.calls], ['begin', 'fetch_all', 'commit'])
        self.assertEqual(db.calls[1][1].params, ([1, 2], None))
        for responses, expected in (([[]], CategoryNotFound),
                                    ([[(7,)], RuntimeError('failed')], RuntimeError),
                                    ([[(7,)], [(1,)], RuntimeError('refresh failed')], RuntimeError)):
            db = Fake(responses)
            with self.assertRaises(expected):
                TaskCategoryStore(db).set_tasks([1], 7)
            self.assertEqual(db.calls[-1][0], 'rollback')
            self.assertNotIn('commit', [m for m, _ in db.calls])

    def test_invalid_input_never_queries(self):
        db = Fake()
        store = TaskCategoryStore(db)
        for ids, category in (([], None), ([1] * 101, None), ([0], None),
                              ([True], None), ([1], 0), ([1], 2**63)):
            with self.assertRaises(CategoryInvalid):
                store.set_tasks(ids, category)
        with self.assertRaises(CategoryInvalid):
            store.rename(1, '')
        with self.assertRaises(CategoryInvalid):
            store.get(True)
        self.assertEqual(db.calls, [])

    def test_commit_error_propagates(self):
        class CommitFailure(Fake):
            @contextmanager
            def transaction(self):
                self.calls.append(('begin', None))
                yield self
                self.calls.append(('commit', None))
                raise RuntimeError('commit failed')
        db = CommitFailure([[(1,)]])
        with self.assertRaisesRegex(RuntimeError, 'commit failed'):
            TaskCategoryStore(db).set_tasks([1], None)

    def test_batch_requires_transaction_support(self):
        class NoTransaction:
            pass
        with self.assertRaisesRegex(TypeError, 'transaction-capable'):
            TaskCategoryStore(NoTransaction()).set_tasks([1], None)


if __name__ == '__main__':
    unittest.main()
