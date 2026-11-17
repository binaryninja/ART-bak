"""Offline regressions for Go's empty bare-quote cursor behavior."""
import unittest

from artex.asset_dsl import DSLError, compile_dsl, parse_dsl
from artex.db.assets import asset_count, asset_query


class EmptyBareQuoteTests(unittest.TestCase):
    def test_empty_prefix_does_not_turn_quoted_text_into_a_field(self):
        for text in ('port=80', 'status>=200', 'domain==example', 'tech=nginx',
                     'task_id=7', 'company_id!=9', '(port=80)', 'unknown=x'):
            with self.subTest(text=text):
                node = parse_dsl('""' + text)
                self.assertEqual((node.kind, node.expr.field, node.expr.op, node.expr.value),
                                 ('leaf', '', '=', text))
                sql, values = compile_dsl('""' + text)
                self.assertEqual(values, ['%' + text + '%'])
                self.assertIn('domain ILIKE $1', sql)
                self.assertIn('unnest(bound_domains)', sql)

    def test_cursor_restarts_at_second_quote_instead_of_losing_closing_quote(self):
        for prefix in ('""', '"""', '""""'):
            with self.subTest(prefix=prefix):
                node = parse_dsl(prefix + 'example"')
                self.assertEqual(node.expr.value, 'example')
                self.assertEqual(compile_dsl(prefix + 'example"')[1], ['%example%'])

    def test_unterminated_second_quote_preserves_spaces_and_operator_text(self):
        for text in (' AND port=80', 'port=80 OR port=443 AND status=200', 'x (y) z'):
            with self.subTest(text=text):
                node = parse_dsl('""' + text)
                self.assertEqual(node.expr.value, text)
                self.assertEqual(compile_dsl('""' + text, 3)[1], ['%' + text + '%'])

    def test_recovered_quoted_keyword_still_uses_upstream_operator_rules(self):
        # Go classifies a quoted bare AND/OR as an operator as well.
        for text in ('""AND"', '""OR"', '""and"', '""or"'):
            with self.subTest(text=text), self.assertRaises(DSLError):
                compile_dsl(text)

    def test_correct_token_boundary_leaves_following_expression_separate(self):
        sql, values = compile_dsl('domain==first AND ""port=80" OR status>=200', 2)
        self.assertEqual(values, ['first', '%port=80%', 200])
        self.assertTrue(sql.startswith('((domain = $3) AND ('))
        self.assertIn('domain ILIKE $4', sql)
        self.assertTrue(sql.endswith(')) OR (status_code >= $5)'))

    def test_field_empty_quotes_remain_values_and_missing_values_remain_bare(self):
        self.assertEqual(compile_dsl('domain=""'), ('domain ILIKE $1', ['%%']))
        self.assertEqual(compile_dsl('tech==""'), ('$1 = ANY(technologies)', ['']))
        self.assertEqual(compile_dsl('port=')[1], ['%port=%'])
        with self.assertRaises(DSLError):
            compile_dsl('port=""')
        for text in ('"', '""', '"""', '""""'):
            with self.subTest(text=text), self.assertRaises(DSLError):
                compile_dsl(text)

    def test_payload_is_bound_once_without_sql_interpolation(self):
        text = "x' OR 1=1 --"
        sql, values = compile_dsl('""' + text, 4)
        self.assertEqual(values, ['%' + text + '%'])
        self.assertNotIn(text, sql)
        self.assertIn('domain ILIKE $5', sql)
        self.assertNotIn('$6', sql)

    def test_unicode_whitespace_difference_remains_intentional(self):
        self.assertEqual(compile_dsl('domain=="café"\u2003AND\u2003port=80')[1], ['café', 80])
        self.assertEqual(compile_dsl('""café\u2003port=80')[1], ['%café\u2003port=80%'])

    def test_query_plans_keep_parenthesized_filters_and_reserved_placeholders(self):
        # Query builders return SQL/values only; no executor or database is used.
        query = asset_query('domain=x OR ""port=80', 'service', 7, in_scope=True)
        count = asset_count('domain=x OR ""port=80', 'service', 7, in_scope=True)
        self.assertEqual(query.params, (7, '%x%', '%port=80%', 'service', 50, 0))
        self.assertEqual(count.params, query.params[:-2])
        self.assertIn('WHERE ((domain ILIKE $2) OR (', query.sql)
        self.assertIn('domain ILIKE $3', query.sql)
        self.assertIn('))) AND type = $4 AND id IN (SELECT id FROM target)', query.sql)
        self.assertTrue(query.sql.endswith('LIMIT $5 OFFSET $6'))


if __name__ == '__main__':
    unittest.main()
