import unittest
from artex.skills import (Catalog, Skill, clean_strs, parse_skill, render_skill,
                          rewrite_skill_frontmatter, skill_name_from_frontmatter,
                          skill_rel_path, valid_skill_name)


class SkillTests(unittest.TestCase):
    def test_names(self):
        for value in ['web-recon', 'a', 'nuclei2', '\u4e2d\u6587', '\u65e5\u672c\u8a9e']:
            self.assertTrue(valid_skill_name(value), value)
        for value in ['', 'Web-Recon', '-lead', 'trail-', 'dou--ble', 'has space',
                      'dot.name', 'a/b', 'a\\b', '.', 'a\x00b', 'a' * 65, '1abc', 'a\u202eb', 'a\ud800']:
            self.assertFalse(valid_skill_name(value), repr(value))
        self.assertTrue(valid_skill_name('a' * 64))

    def test_paths(self):
        for value, expected in [('SKILL.md', 'SKILL.md'), ('./SKILL.md', 'SKILL.md'),
                                ('references/a b.txt', 'references/a b.txt'),
                                ('assets/image-1_v2.png', 'assets/image-1_v2.png')]:
            self.assertEqual(skill_rel_path(value), expected)
        for value in ['', '../etc/passwd', 'a/../../b', '/abs/path', 'a//b', '..\\x',
                      '%2e%2e/x', 'a\x00b', 'a\u00a0b', 'a\u202eb', 'a#b', 'a?b', 'a:b',
                      'a' * 513, '\u4e2d' * 171, 'a\ud800', '.', 'a..b']:
            with self.assertRaises(ValueError, msg=repr(value)):
                skill_rel_path(value)

    def test_clean(self):
        self.assertEqual(clean_strs([' x ', '', '  ', 'x']), ['x', 'x'])

    def test_name_extraction(self):
        self.assertEqual(skill_name_from_frontmatter('---\nname: "demo"\n---\n'), 'demo')
        self.assertEqual(skill_name_from_frontmatter('body'), '')
        self.assertEqual(skill_name_from_frontmatter('heading\n---\nname: demo\n---'), 'demo')

    def test_rewrite_preserves_body_and_unknown(self):
        body = '## Body\n\n x: y\r\n'
        source = '---\nname: demo\nx-extra: yes\ndescription: before\nmcps: old\n---\n' + body
        result = rewrite_skill_frontmatter(source, description=' after ', mcps=[' new ', ''])
        self.assertIn('x-extra: yes\n', result)
        self.assertIn('description: after\n', result)
        self.assertIn('mcps: new\n', result)
        self.assertTrue(result.endswith(body))
        self.assertNotIn('mcps:', rewrite_skill_frontmatter(source, mcps=[]))
        self.assertIn('mcps: old', rewrite_skill_frontmatter(source))
        self.assertIn('license: \n', rewrite_skill_frontmatter(source, license=''))

    def test_rewrite_rejects_missing_frontmatter_and_injection(self):
        for source in ['body', '---\nname: demo']:
            with self.assertRaises(ValueError):
                rewrite_skill_frontmatter(source)
        for kwargs in [dict(description='x\nname: changed'), dict(mcps=['a\nb'])]:
            with self.assertRaises(ValueError):
                rewrite_skill_frontmatter('---\nname: demo\n---\n', **kwargs)

    def test_render_parse_roundtrip(self):
        body = 'Untrusted text only: never execute this.\n'
        skill = parse_skill(render_skill('demo', 'Description: with # punctuation',
                                        license='MIT', compatibility='offline',
                                        mcps=[' a ', 'b'], instructions=body))
        self.assertEqual(skill.name, 'demo')
        self.assertEqual(skill.description, 'Description: with # punctuation')
        self.assertEqual(skill.instructions, body)
        self.assertEqual(skill.mcps, ('a', 'b'))
        self.assertEqual(skill.license, 'MIT')
        self.assertIn('1. ', render_skill('demo', 'description'))

    def test_render_rejections(self):
        for name, description in [('Bad', 'x'), ('good', ''), ('good', '  '), ('good', 'a\nb')]:
            with self.assertRaises(ValueError):
                render_skill(name, description)

    def test_parse_rejects_complex_frontmatter(self):
        for metadata in ['description: |\n  block', 'description: [list]',
                         'description: !tag x', 'description: x\nname: duplicate',
                         'description: "unclosed', 'description: x\n  nested: y']:
            with self.assertRaises(ValueError):
                parse_skill('---\nname: demo\n' + metadata + '\n---\n')

    def test_scalar_forms(self):
        skill = parse_skill("---\nname: demo\ndescription: 'don''t'\nmcps: a, b # note\n---\n")
        self.assertEqual(skill.description, "don't")
        self.assertEqual(skill.mcps, ('a', 'b'))

    def test_catalog(self):
        catalog = Catalog([Skill('z', 'last'), Skill('a', 'first')])
        self.assertEqual([s.name for s in catalog.list()], ['a', 'z'])
        self.assertIsNone(catalog.get('missing'))
        self.assertEqual([s.name for s in catalog.select(['z', 'missing', 'z']).list()], ['z'])
        with self.assertRaises(ValueError):
            catalog.add(Skill('a', 'duplicate'))
        with self.assertRaises(ValueError):
            catalog.add(Skill('../escape', 'bad'))


if __name__ == '__main__':
    unittest.main()
