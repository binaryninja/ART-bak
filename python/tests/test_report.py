import csv
from datetime import datetime
import io
import unittest
from artex.report import Finding, TrafficBinding, finding_filename, findings_csv, findings_markdown, single_finding_markdown, markdown


class ReportTests(unittest.TestCase):
    now = datetime(2026, 11, 15, 12)

    def test_empty_reports(self):
        self.assertIn('No matching vulnerabilities', findings_markdown([], self.now))
        self.assertIn('No vulnerabilities confirmed', markdown('', '', self.now, {}, []))

    def test_sort_and_nonmutation(self):
        low = Finding(1, self.now, name='Low', severity='low')
        high = Finding(2, self.now, name='High', severity='high')
        unknown = Finding(3, self.now, name='Other', severity='unexpected')
        items = [low, unknown, high]
        text = findings_markdown(items, self.now)
        self.assertLess(text.index('[HIGH]'), text.index('[LOW]'))
        self.assertLess(text.index('[LOW]'), text.index('[UNEXPECTED]'))
        self.assertEqual(items, [low, unknown, high])

    def test_evidence_and_staleness(self):
        f = Finding(1, self.now, evidence='```\nmalformed evidence', report='Details',
                    evidence_version=2, report_evidence_version=1,
                    traffic_bindings=[TrafficBinding(7, url='https://example.test/`', method='GET')])
        text = single_finding_markdown(f, self.now)
        self.assertIn('````\n```\nmalformed evidence\n````', text)
        self.assertIn('needs updating', text)
        self.assertIn('evidence/1/7/request.http', text)
        self.assertIn('%60', text)

    def test_filename_safe_unicode(self):
        name = finding_filename(Finding(7, self.now, name='../' + '界' * 200))
        self.assertNotIn('/', name)
        self.assertNotIn('\\', name)
        self.assertLessEqual(len(name[:-3].encode()), 120)
        self.assertTrue(name.endswith('.md'))

    def test_csv_formula_quotes_bom(self):
        output = findings_csv([Finding(1, self.now, name='=1+1', summary='line 1\nline 2, "quoted"')])
        self.assertTrue(output.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.reader(io.StringIO(output.decode('utf-8-sig'))))
        self.assertEqual(rows[1][1], "'=1+1")
        self.assertEqual(rows[1][7], 'line 1\nline 2, "quoted"')

    def test_filename_long_titles_keep_unique_id(self):
        a = finding_filename(Finding(1, self.now, name='a' * 200))
        b = finding_filename(Finding(2, self.now, name='a' * 200))
        self.assertNotEqual(a, b)
        self.assertTrue(a.endswith('_1.md'))
        with self.assertRaises(ValueError):
            finding_filename(Finding('../escape', self.now))

    def test_csv_formula_prefixes(self):
        for value in ('=1', '+1', '-1', '@SUM(A1)', '  =1', '\tplain', '\rplain', '\nplain'):
            with self.subTest(value=value):
                output = findings_csv([Finding(1, self.now, name=value)])
                rows = list(csv.reader(io.StringIO(output.decode('utf-8-sig'))))
                self.assertEqual(rows[1][1], "'" + value)

    def test_malformed_exploration_values(self):
        text = markdown('Task', 'Goal', self.now, {}, [
            {'severity': [], 'name': {}, 'evidence': {'poc': []}},
            {'severity': {}, 'summary': None},
            None, [], b'\xff', '{"severity":null}',
        ])
        self.assertEqual(text.count('[INFO] Unclassified'), 6)

    def test_exploration_payloads(self):
        text = markdown('Task', 'Goal', self.now, {'service': 2, 'domain': 1},
                        ['{"name":"Issue","severity":"high","evidence":{"poc":"sample"}}', 'invalid'])
        self.assertIn('domain 1, service 2', text)
        self.assertIn('[HIGH] Issue', text)
        self.assertIn('sample', text)


if __name__ == '__main__':
    unittest.main()
