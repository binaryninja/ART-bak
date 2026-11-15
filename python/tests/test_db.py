import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from artex.db import (asset_query, asset_count, assets_by_ids, decode_asset,
                      ConstraintStore, ConstraintNotFound)
from artex.db.sql import ASSET_SELECT, SCOPE_TARGET_CTE


class FakeExecutor:
    def __init__(self):
        self.queries = []
        self.rows = []
        self.one = (9,)
        self.affected = 1
        self.error = None

    def _record(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error

    def fetch_all(self, query):
        self._record(query)
        return self.rows

    def fetch_one(self, query):
        self._record(query)
        return self.one

    def execute(self, query):
        self._record(query)
        return self.affected


class AssetQueryTests(unittest.TestCase):
    def test_default_pagination(self):
        q = asset_query(limit=-1, offset=-1)
        self.assertEqual(q.params, (50, 0))
        self.assertTrue(q.sql.endswith("WHERE (1=1) ORDER BY last_seen DESC, id DESC LIMIT $1 OFFSET $2"))

    def test_type_and_task_filters_bind_parameters(self):
        q = asset_query("port==443", "service", 7, 10, 2)
        self.assertEqual(q.params, (443, "service", 7, 10, 2))
        self.assertIn("WHERE (port = $1) AND type = $2 AND $3 = ANY(task_ids)", q.sql)

    def test_or_is_parenthesized_before_any_scope_constraint(self):
        q = asset_query("port=80 OR port=443", "service", 7)
        self.assertIn("WHERE ((port = $1) OR (port = $2)) AND type = $3 AND $4 = ANY(task_ids)", q.sql)

    def test_scope_reserves_first_placeholder(self):
        q = asset_query("port=80 OR port=443", "service", 7, in_scope=True)
        self.assertEqual(q.params, (7, 80, 443, "service", 50, 0))
        self.assertTrue(q.sql.startswith("WITH " + SCOPE_TARGET_CTE))
        self.assertIn("WHERE ((port = $2) OR (port = $3)) AND type = $4 AND id IN (SELECT id FROM target)", q.sql)
        self.assertNotIn("ANY(task_ids)", q.sql)

    def test_no_scope_id_falls_back_to_global(self):
        self.assertEqual(asset_query(task_id=0, in_scope=True), asset_query())
        self.assertEqual(asset_query(task_id=-1, in_scope=True), asset_query())

    def test_count_and_page_share_filters(self):
        for scoped in (False, True):
            q = asset_query("tech==nginx", "service", 7, in_scope=scoped)
            c = asset_count("tech==nginx", "service", 7, in_scope=scoped)
            self.assertEqual(c.params, q.params[:-2])
            self.assertEqual(c.sql.split(" WHERE ")[-1], q.sql.split(" WHERE ")[-1].split(" ORDER BY ")[0])

    def test_injection_payload_remains_data(self):
        payload = "service' OR TRUE --"
        q = asset_query('domain=="a\'b"', payload, 1)
        self.assertNotIn(payload, q.sql)
        self.assertNotIn("a'b", q.sql)
        self.assertEqual(q.params[:2], ("a'b", payload))

    def test_ids_have_no_unbound_values(self):
        q = assets_by_ids([3, 9], task_id=2)
        self.assertEqual(q.params, (2, 3, 9))
        self.assertIn("WHERE id IN ($2,$3) AND id IN (SELECT id FROM target)", q.sql)

    def test_empty_ids_never_broaden(self):
        self.assertIn("WHERE FALSE", assets_by_ids([]).sql)
        self.assertIn("WHERE FALSE AND", assets_by_ids([], task_id=4).sql)

    def test_integer_contracts(self):
        for bad in (True, 1.2, "3", 2**63):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                asset_query(task_id=bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                assets_by_ids([bad])
        with self.assertRaises(ValueError):
            asset_query(typ=3)

    def test_sql_contracts_match_upstream(self):
        root = Path(__file__).resolve().parents[2]
        for path, name, actual in (("db/assets.go", "assetSelectCols", ASSET_SELECT),
                                   ("db/task_assets_context.go", "scopeTargetCTE", SCOPE_TARGET_CTE)):
            original = re.search(r"const " + name + r" = `(.*?)`",
                                 (root / path).read_text(), re.S).group(1)
            self.assertEqual(original, actual)


class AssetDecodeTests(unittest.TestCase):
    def row(self):
        row = [None] * 32
        row[0:4] = [1, "service", None, "[7,8]"]
        row[10] = b'["example.test"]'
        row[11] = '[{"port":443}]'
        row[26] = '["nginx"]'
        row[30] = '{"nested":{"x":1}}'
        row[31] = "2026-11-15 00:00:00+00"
        return row

    def test_json_and_nullable_columns(self):
        out = decode_asset(self.row())
        self.assertEqual(out["task_ids"], [7, 8])
        self.assertEqual(out["open_ports"], [{"port": 443}])
        self.assertIsNone(out["company_id"])
        self.assertIsNone(out["auth"])
        self.assertEqual(out["last_seen"], "2026-11-15 00:00:00+00")

    def test_empty_is_distinct_from_null(self):
        row = self.row()
        row[27] = "[]"
        self.assertEqual(decode_asset(row)["auth"], [])
        row[27] = "null"
        self.assertIsNone(decode_asset(row)["auth"])

    def test_malformed_and_incorrect_shapes_fail(self):
        for index, raw in ((3, "[true]"), (3, "{}"), (30, "[]"), (11, '["x"]'), (26, "bad")):
            row = self.row()
            row[index] = raw
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_asset(row)

    def test_already_decoded_mutables_are_copied(self):
        row = self.row()
        row[30] = {"x": [1]}
        decoded = decode_asset(row)
        decoded["extra"]["x"].append(2)
        self.assertEqual(row[30], {"x": [1]})

    def test_column_width_is_exact(self):
        with self.assertRaises(ValueError):
            decode_asset(self.row()[:-1])


class ConstraintStoreTests(unittest.TestCase):
    def setUp(self):
        self.executor = FakeExecutor()
        self.store = ConstraintStore(self.executor, 42)

    def test_list_preserves_order_and_timestamp(self):
        now = datetime.now(timezone.utc)
        self.executor.rows = [(1, "allow", "read", "", now), (2, "deny", "write", "human", now)]
        rows = self.store.list()
        self.assertEqual([r.id for r in rows], [1, 2])
        self.assertEqual(rows[0].created_at, now)
        self.assertEqual(self.executor.queries[0].params, (42,))
        self.assertIn("ORDER BY (kind='deny'), id", self.executor.queries[0].sql)

    def test_add_default_origin(self):
        self.assertEqual(self.store.add("allow", "read"), 9)
        self.assertEqual(self.executor.queries[-1].params, (42, "allow", "read", "system"))

    def test_add_preserves_origin_and_binds_text(self):
        payload = "'; DROP TABLE task_constraints; --"
        self.store.add("deny", payload, "human")
        q = self.executor.queries[-1]
        self.assertEqual(q.params, (42, "deny", payload, "human"))
        self.assertNotIn(payload, q.sql)

    def test_updates_and_deletes_are_exploration_scoped(self):
        self.store.update(9, "deny", "write")
        self.assertEqual(self.executor.queries[-1].params, ("deny", "write", 9, 42))
        self.assertIn("WHERE id=$3 AND exploration_id=$4", self.executor.queries[-1].sql)
        self.store.delete(9)
        self.assertEqual(self.executor.queries[-1].params, (9, 42))
        self.assertIn("WHERE id=$1 AND exploration_id=$2", self.executor.queries[-1].sql)

    def test_not_found_for_update_and_delete(self):
        self.executor.affected = 0
        with self.assertRaises(ConstraintNotFound):
            self.store.update(8, "deny", "")
        with self.assertRaises(ConstraintNotFound):
            self.store.delete(8)

    def test_invalid_kind_never_reaches_executor(self):
        for kind in ("ALLOW", "", "permit"):
            with self.assertRaises(ValueError):
                self.store.add(kind, "")
            with self.assertRaises(ValueError):
                self.store.update(1, kind, "")
        self.assertEqual(self.executor.queries, [])

    def test_database_errors_propagate(self):
        self.executor.error = RuntimeError("fixture failure")
        for operation in (self.store.list, lambda: self.store.add("allow", ""),
                          lambda: self.store.update(1, "deny", ""), lambda: self.store.delete(1)):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                operation()
