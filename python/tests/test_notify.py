import base64
import hashlib
import hmac
import unittest
from urllib.parse import parse_qs, urlsplit
from artex.notify import *


class NotifyTests(unittest.TestCase):
    def test_snapshot_and_item_defaults(self):
        self.assertEqual(Item().title(), '(unnamed finding)')
        self.assertEqual(Item(vulnclass='XSS').title(), 'XSS')
        self.assertEqual(Item(name='Title', vulnclass='XSS').title(), 'Title')
        self.assertTrue(Item(from_status='pending').is_status_change())
        a, b = Snapshot(), Snapshot()
        a.asset_ids.append(1)
        self.assertEqual(b.asset_ids, [])

    def test_filter_malformed(self):
        for raw in ('', '{', '[1]', 'null', '{"min_severity":123}', '{"task_ids":[true]}'):
            self.assertEqual(parse_filter(raw), Filter())
        self.assertEqual(parse_filter('{"min_severity":"high","unknown":1}'), Filter(min_severity='high'))
        self.assertEqual(parse_filter('{"task_ids":null}'), Filter())

    def test_filter_thresholds(self):
        self.assertTrue(at_least('unknown', ''))
        self.assertFalse(at_least('unknown', 'low'))
        self.assertTrue(at_least('critical', 'high'))
        self.assertTrue(at_least('low', 'invalid'))
        with self.assertRaises(ValueError):
            Filter(min_severity='invalid').validate()

    def test_filter_scope_keywords_status(self):
        s = Snapshot(task_id=7, asset_ids=[10,20], vulnclass='SQL injection', severity='high')
        self.assertTrue(match(Filter(task_ids=[7], asset_ids=[20]), s))
        self.assertFalse(match(Filter(task_ids=[8]), s))
        self.assertFalse(match(Filter(asset_ids=[99]), s))
        self.assertTrue(match(Filter(vulnclass_include=[' sql ']), s))
        self.assertFalse(match(Filter(vulnclass_include=['SQL'], vulnclass_exclude=['injection']), s))
        self.assertFalse(match(Filter(vulnclass_include=[' ']), s))
        s.kind = EVENT_FINDING_STATUS_CHANGED
        self.assertFalse(match(Filter(), s))
        self.assertTrue(match(Filter(on_status_change=True), s))

    def test_masking(self):
        cfg = {'url':'https://host/secret123', 'headers':{'Authorization':'secret'}, 'method':'POST'}
        masked = mask_config('webhook', cfg)
        self.assertTrue(is_masked(masked['url']))
        self.assertEqual(masked['headers'], MASKED_PREFIX)
        self.assertEqual(masked['method'], 'POST')
        self.assertEqual(mask_config('unknown', cfg), {})
        self.assertEqual(cfg['headers']['Authorization'], 'secret')
        self.assertEqual(masked_value('short'), MASKED_PREFIX)

    def test_merge(self):
        self.assertEqual(merge_config({'a':'secret','b':1,'c':2}, {'a':MASKED_PREFIX,'b':'  ','d':3}),
                         {'a':'secret','c':2,'d':3})

    def test_destination_requires_explicit_credentials(self):
        stored = {'url':'https://old', 'headers':{'Authorization':'secret'}}
        with self.assertRaises(DestinationChangedWithoutCredentials) as ctx:
            prepare_config_update('webhook', stored, {'url':'https://new'})
        self.assertEqual(ctx.exception.missing, ['headers'])
        result = prepare_config_update('webhook', stored, {'url':'https://new','headers':''})
        self.assertEqual(result, {'url':'https://new'})
        with self.assertRaises(DestinationChangedWithoutCredentials):
            prepare_config_update('email', {'host':'old','password':'x'}, {'host':'new','password':MASKED_PREFIX})

    def test_nested_mask_rejected_without_destination_change(self):
        with self.assertRaises(ValueError):
            prepare_config_update('webhook', {}, {'headers':{'Authorization':MASKED_PREFIX}})
        with self.assertRaises(ValueError):
            prepare_config_update('unknown', {}, {})

    def test_blank_destination_and_number_equivalence(self):
        self.assertEqual(prepare_config_update('telegram', {'bot_token':'secret'},
                         {'base_url':' ', 'bot_token':MASKED_PREFIX}), {'bot_token':'secret'})
        self.assertEqual(prepare_config_update('email', {'port':587.0,'password':'secret'},
                         {'port':587,'password':MASKED_PREFIX})['password'], 'secret')

    def test_utf8_truncation(self):
        for maximum in range(1, 20):
            self.assertLessEqual(len(truncate_bytes('😀'*12, maximum).encode()), maximum)
        self.assertEqual(truncate_runes('abc', 1), 'a')
        self.assertEqual(truncate_runes('abcd', 3), 'ab…')
        self.assertEqual(one_line('a\n b\t c'), 'a b c')
        self.assertEqual(truncate_html('abc<a href="long">hello</a>', 10), 'abc')
        self.assertEqual(truncate_html('abc&amp;rest', 7), 'abc')

    def test_packing_preserves_first_oversized_item(self):
        render = lambda item, index: item
        self.assertEqual(pack_item_count(['a'*100, 'b'], 10, 0, '', len, render), 1)
        self.assertEqual(pack_item_count([], 10, 0, '', len, render), 0)
        self.assertEqual(pack_item_count(['abc','def','ghi'], 6, 0, '', len, render), 2)
        self.assertEqual(pack_item_count(['abc','def'], 0, 100, '', len, render), 2)

    def test_untrusted_markdown(self):
        self.assertEqual(markdown_text('hello\n![x](url)'), r'hello \!\[x\]\(url\)')

    def test_markdown_renderer_and_delivered_count(self):
        message = Message(items=[Item(name='![x](evil)', severity='high', from_status='pending', to_status='fixed')])
        body, count = markdown_body(message)
        self.assertEqual(count, 1)
        self.assertIn(r'\!\[x\]', body)
        self.assertIn('Pending → Fixed', body)
        message = Message(items=[Item(name='x' * 500, severity='high') for _ in range(4)], batch=True)
        body, count = markdown_body(message, 1000)
        self.assertEqual(count, 1)
        self.assertLessEqual(len(body.encode()), 1000)
        self.assertIn('remaining 3', body)
        self.assertEqual(markdown_body(Message()), ('', 0))

    def test_signatures(self):
        expected = base64.b64encode(hmac.new(b'123\nsecret', b'', hashlib.sha256).digest()).decode()
        self.assertEqual(feishu_sign('123','secret'), expected)
        signed = ding_talk_signed_url('https://example.test/hook?access_token=x', 'secret', 123)
        query = parse_qs(urlsplit(signed).query)
        expected = base64.b64encode(hmac.new(b'secret', b'123\nsecret', hashlib.sha256).digest()).decode()
        self.assertEqual(query['sign'], [expected])
        self.assertEqual(query['timestamp'], ['123'])
        self.assertEqual(query['access_token'], ['x'])

    def test_ssrf_literals_and_redacted_errors(self):
        for address in ('127.0.0.1','::1','::ffff:127.0.0.1','169.254.169.254','0.0.0.0','224.0.0.1'):
            self.assertTrue(is_blocked_dial_ip(address))
        self.assertFalse(is_blocked_dial_ip('192.168.1.2'))
        self.assertFalse(is_blocked_dial_ip('8.8.8.8'))
        for url in ('https://127.0.0.1/SECRET','file:///SECRET','https://[broken/SECRET'):
            with self.assertRaises(ValueError) as ctx:
                validate_http_url(url)
            self.assertNotIn('SECRET', str(ctx.exception))
        validate_http_url('https://192.168.1.2')
        validate_http_url('https://127.0.0.1', allow_local_targets=True)


if __name__ == '__main__':
    unittest.main()
