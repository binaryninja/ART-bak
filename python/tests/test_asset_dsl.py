import unittest
from artex.asset_dsl import DSLError, Expr, Node, build_dsl_where, compile_dsl, validate_dsl


class AssetDSLTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(compile_dsl(' \t\n'), ('1=1', []))

    def test_string_aliases_operators_and_bound_values(self):
        self.assertEqual(compile_dsl('TITLE="home page" AND domain!=example AND method==GET'),
                         ('(page_title ILIKE $1) AND (domain NOT ILIKE $2) AND (method = $3)',
                          ['%home page%', '%example%', 'GET']))

    def test_precedence_and_grouping(self):
        self.assertEqual(compile_dsl('port=80 OR port=443 AND status>=200')[0],
                         '(port = $1) OR ((port = $2) AND (status_code >= $3))')
        self.assertEqual(compile_dsl('(port=80 OR port=443) AND status<400')[0],
                         '((port = $1) OR (port = $2)) AND (status_code < $3)')

    def test_parameter_offset(self):
        self.assertEqual(compile_dsl('port==443 AND status!=500', 3),
                         ('(port = $4) AND (status_code != $5)', [443, 500]))

    def test_array_semantics(self):
        for text, expected, value in (
            ('tech=nginx', 'EXISTS (SELECT 1 FROM unnest(technologies) t(v) WHERE v ILIKE $1)', '%nginx%'),
            ('technology==nginx', '$1 = ANY(technologies)', 'nginx'),
            ('technologies!=nginx', 'NOT ($1 = ANY(technologies))', 'nginx')):
            self.assertEqual(compile_dsl(text), (expected, [value]))

    def test_task_company_operator_compatibility(self):
        self.assertEqual(compile_dsl('task_id!=42 AND company_id>7'),
                         ('($1 = ANY(task_ids)) AND (company_id = $2)', [42, 7]))

    def test_unknown_fields_are_bare_text_not_sql(self):
        sql, args = compile_dsl('type=service')
        self.assertEqual(args, ['%type=service%'])
        self.assertIn('domain ILIKE $1', sql)
        self.assertIn('unnest(bound_domains)', sql)
        self.assertNotIn('type=service', sql)

    def test_quote_injection_is_bound(self):
        payload = "x' OR 1=1 --"
        sql, args = compile_dsl('domain=="' + payload + '"')
        self.assertEqual((sql, args), ('domain = $1', [payload]))

    def test_numeric_validation_and_int64(self):
        for value in ('1_000', '1.2', '１２', '9223372036854775808', '" 3"', '9' * 5000):
            with self.subTest(value=value), self.assertRaises(DSLError):
                compile_dsl('port=' + value)
        self.assertEqual(compile_dsl('task_id=-9223372036854775808')[1], [-9223372036854775808])

    def test_invalid_syntax_and_operators(self):
        for text in ('foo bar', '(port=80', 'port=80)', 'port=80 AND', 'OR port=80', '()', 'domain>foo', 'tech<=x'):
            with self.subTest(text=text), self.assertRaises(DSLError):
                validate_dsl(text)

    def test_upstream_lexical_quirks_documented(self):
        self.assertEqual(compile_dsl('domain="unfinished')[1], ['%unfinished%'])
        self.assertEqual(compile_dsl('domain=""'), ('domain ILIKE $1', ['%%']))
        with self.assertRaises(DSLError):
            compile_dsl('"AND"')
        self.assertEqual(compile_dsl('domain=')[1], ['%domain=%'])

    def test_unicode_text_and_whitespace(self):
        self.assertEqual(compile_dsl('title=="café"\u2003AND\u2003port=80')[1], ['café', 80])

    def test_ast_validation_and_base(self):
        with self.assertRaises(DSLError):
            build_dsl_where(Node('leaf', expr=Expr('untrusted; DROP TABLE assets', '=', 'x')))
        for base in (-1, 1.2, '1', True):
            with self.subTest(base=base), self.assertRaises(DSLError):
                compile_dsl('port=80', base)

    def test_deep_nesting_is_validation_error(self):
        with self.assertRaises(DSLError):
            compile_dsl('(' * 2000 + 'port=80' + ')' * 2000)


if __name__ == '__main__':
    unittest.main()
