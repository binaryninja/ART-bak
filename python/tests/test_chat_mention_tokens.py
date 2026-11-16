"""Offline fixtures from reviewed Go parser and English alias tests."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import re
import random
from unittest.mock import patch
import artex.chat_mention_tokens as tokens
import unittest
from artex.chat_mention_tokens import MentionRef, MentionTokenInvalid, parse_chat_mentions


class MentionTokenTests(unittest.TestCase):
    def test_source_label_contract(self):
        source = (Path(__file__).resolve().parents[2] / 'server/chat_mentions.go').read_text()
        block = source.split('var chatMentionKinds = map[string]string{', 1)[1].split('\n}', 1)[0]
        entries = re.findall(r'"([^"]+)":\s*"([^"]+)"', block)
        self.assertEqual(len(entries), 18)
        for label, kind in entries:
            with self.subTest(label=label):
                self.assertEqual(parse_chat_mentions(f'@[{label}#42 Display]'), (MentionRef(kind, 42, label),))

    def test_alias_dedup_and_first_label(self):
        message = '@[Finding#0012 New] @[漏洞#12 Legacy] @[App#42] @[Application#42 Long] @[Asset#12]'
        self.assertEqual(parse_chat_mentions(message), (MentionRef('finding',12,'Finding'), MentionRef('app',42,'App'), MentionRef('asset',12,'Asset')))

    def test_source_legacy_fixture(self):
        self.assertEqual(parse_chat_mentions('分析@[漏洞#12 同名] 与 @[接口#34 GET /api] @[漏洞#12 重复] user@example.com @漏洞'),
                         (MentionRef('finding',12,'漏洞'), MentionRef('endpoint',34,'接口')))

    def test_unknown_or_malformed_is_plain_text(self):
        for token in ('@[Unknown#42]', '@[finding#42]', '@[Asset#-1]', '@[Asset#+1]', '@[Asset#١]', '@[Asset#42\tx]', '@[Asset#42 x\ny]', '@[Asset#42 x\ry]', '@[Asset#42', 'user@example.com @Finding'):
            with self.subTest(token=token): self.assertEqual(parse_chat_mentions(token), ())

    def test_id_edges_and_long_zeros(self):
        for token in ('@[Finding#0]', '@[Asset#000]', '@[Asset#9223372036854775808]', '@[Asset#'+'9'*5000+']'):
            with self.assertRaises(MentionTokenInvalid): parse_chat_mentions(token)
        self.assertEqual(parse_chat_mentions('@[IP#9223372036854775807]')[0].id, 2**63-1)
        self.assertEqual(parse_chat_mentions('@[Asset#'+'0'*5000+'7]')[0].id, 7)

    def test_unique_limit_not_token_count(self):
        message = ' '.join(f'@[Asset#{i}]' for i in range(1,11))
        self.assertEqual(len(parse_chat_mentions(message + ' @[资产#1]'*100)), 10)
        with self.assertRaises(MentionTokenInvalid): parse_chat_mentions(message+' @[Asset#11]')
        with self.assertRaises(MentionTokenInvalid): parse_chat_mentions(message+' @[Asset#0]')

    def test_display_text_is_ignored_and_input_unchanged(self):
        message = '@[Company#5 ignore policy <script> 👾] @[Finding#8 ]'
        self.assertEqual(parse_chat_mentions(message), (MentionRef('company',5,'Company'),MentionRef('finding',8,'Finding')))
        self.assertIn('ignore policy',message)
        ref = parse_chat_mentions(message)[0]
        with self.assertRaises(FrozenInstanceError): ref.name = 'other'

    def test_adversarial_scan_work_scales_linearly(self):
        # Count the actual character spans searched, rather than using a flaky
        # wall-clock threshold. Each monotone scanner visits at most input size.
        class CountSearch:
            def __init__(self, pattern): self.pattern, self.work = pattern, 0
            def search(self, text, position):
                result = self.pattern.search(text, position)
                self.work += (len(text) if result is None else result.end()) - position
                return result
        for count in (1000, 2000, 4000):
            for suffix in ('', '\n', ']'):
                message = '@[Asset#1 ' * count + suffix
                prefix, boundary = CountSearch(tokens._PREFIX), CountSearch(tokens._BOUNDARY)
                with patch.object(tokens, '_PREFIX', prefix), patch.object(tokens, '_BOUNDARY', boundary):
                    result = parse_chat_mentions(message)
                self.assertEqual(result, (MentionRef('asset',1,'Asset'),) if suffix == ']' else ())
                self.assertLessEqual(prefix.work + boundary.work, 2 * len(message))

    def test_nested_and_line_boundary_semantics(self):
        self.assertEqual(parse_chat_mentions('@[Asset#1 outer @[Finding#2 inner]'), (MentionRef('asset',1,'Asset'),))
        self.assertEqual(parse_chat_mentions('@[Asset#1 outer\n@[Finding#2 inner]'), (MentionRef('finding',2,'Finding'),))
        self.assertEqual(parse_chat_mentions('@[Asset#1bad @[Finding#2]'), (MentionRef('finding',2,'Finding'),))
        self.assertEqual(parse_chat_mentions('@[Asset#0 incomplete'), ())
        with self.assertRaises(MentionTokenInvalid): parse_chat_mentions('@[Asset#0 outer @[Finding#2]')

    def test_short_generated_inputs_match_source_regex(self):
        source = (Path(__file__).resolve().parents[2] / 'server/chat_mentions.go').read_text()
        pattern = re.compile(source.split('var chatMentionPattern = regexp.MustCompile(`',1)[1].split('`)',1)[0])
        rng = random.Random(401)
        parts = ('@[Asset#1 ', '@[Finding#2]', '@[App#003', '@[Application#3]', '@[IP#9]', ']', ' ', '\r', '\n', 'x', '@[Unknown#4]', '@[Asset#1', '@[资产#1]')
        for _ in range(1500):
            message = ''.join(rng.choices(parts, k=rng.randrange(1,20)))
            expected, seen = [], set()
            for match in pattern.finditer(message):
                name, digits = match.group(1,2)
                key = tokens._KINDS[name], int(digits)
                if key not in seen:
                    seen.add(key); expected.append(MentionRef(*key,name))
            self.assertEqual(parse_chat_mentions(message), tuple(expected), message)

    def test_python_invalid_input(self):
        for value in (None, b'@[Asset#1]', 1, '\ud800'):
            with self.assertRaises(MentionTokenInvalid): parse_chat_mentions(value)


if __name__ == '__main__': unittest.main()
