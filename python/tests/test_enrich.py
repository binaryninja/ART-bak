import io
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from artex.enrich import (BODY_LIMIT, COOLDOWN, DNSRecords, Engine, HTTPClient,
                          UpsertIPReq, _Job, _NoRedirect, extract_title, host_of,
                          is_blank, uniq)


class EnrichTests(unittest.TestCase):
    def engine(self, **kwargs):
        store = Mock()
        engine = Engine(store, workers=1, **kwargs)
        self.addCleanup(engine.close)
        return engine, store

    def test_helpers(self):
        self.assertEqual(extract_title(b'<TITLE class="x"> A &amp; B\n </TITLE>'), 'A & B')
        self.assertEqual(extract_title(b'no title'), '')
        self.assertEqual(host_of('https://[::1]:443/a'), '::1')
        self.assertEqual(host_of('https://[bad'), '')
        self.assertEqual(host_of('relative'), '')
        self.assertEqual(uniq(['a', 'b', 'a']), ['a', 'b'])
        for value in (None, '', 0, 0.0):
            self.assertTrue(is_blank(value))
        for value in (False, [], '0', 1):
            self.assertFalse(is_blank(value))

    def test_dns_deduplicates_ips_but_preserves_record_upserts(self):
        resolver = Mock()
        resolver.query_multiple.return_value = DNSRecords(['1.2.3.4', '1.2.3.4'], ['::1'], ['alias.example'])
        engine, store = self.engine(resolver=resolver)
        engine._do_dns(1, 'host.example')
        self.assertEqual([x.args[0] for x in store.upsert_ip.call_args_list],
                         [UpsertIPReq('1.2.3.4', ['host.example']), UpsertIPReq('::1', ['host.example'])])
        self.assertEqual([x.args[0].record_type for x in store.upsert_subdomain.call_args_list],
                         ['A', 'A', 'AAAA', 'CNAME'])

    def test_dns_disabled_failure_and_store_failure_are_best_effort(self):
        engine, store = self.engine()
        engine._do_dns(1, 'host.example')
        store.upsert_ip.assert_not_called()
        engine.resolver = Mock()
        engine.resolver.query_multiple.side_effect = RuntimeError('dns unavailable')
        engine._do_dns(1, 'host.example')
        store.upsert_ip.assert_not_called()
        engine.resolver.query_multiple.side_effect = None
        engine.resolver.query_multiple.return_value = DNSRecords(['1', '2'])
        store.upsert_ip.side_effect = RuntimeError('store unavailable')
        engine._do_dns(1, 'host.example')
        self.assertEqual(store.upsert_ip.call_count, 2)
        self.assertEqual(store.upsert_subdomain.call_count, 2)

    def test_http_bounded_body_and_close(self):
        response = Mock()
        response.status = 404
        body = b'<title>Not &amp; found</title>' + b'x' * BODY_LIMIT
        response.read.side_effect = lambda size: body[:size]
        client = Mock()
        client.get.return_value = response
        engine, store = self.engine(client=client)
        engine._do_http(5, 'https://example.test')
        request = store.upsert_http_service.call_args.args[0]
        self.assertEqual((request.status_code, request.content_length, request.page_title),
                         (404, BODY_LIMIT, 'Not & found'))
        response.read.assert_called_once_with(BODY_LIMIT)
        response.close.assert_called_once()

    def test_http_invalid_url_and_errors(self):
        client = Mock()
        engine, store = self.engine(client=client)
        engine._do_http(1, 'relative')
        client.get.assert_not_called()
        client.get.side_effect = OSError('unavailable')
        engine._do_http(1, 'http://example.test')
        store.upsert_http_service.assert_not_called()
        client.get.side_effect = None
        client.get.return_value.read.side_effect = OSError('read error')
        engine._do_http(1, 'http://example.test')
        client.get.return_value.close.assert_called_once()

    def test_cooldown_kind_and_asset_keys_and_boundary(self):
        now = [0.0]
        engine, _ = self.engine(clock=lambda: now[0])
        job = _Job('dns', 1, 'host')
        self.assertFalse(engine._on_cooldown(job))
        self.assertTrue(engine._on_cooldown(job))
        self.assertFalse(engine._on_cooldown(_Job('http', 1, 'url')))
        self.assertFalse(engine._on_cooldown(_Job('dns', 2, 'host')))
        now[0] = COOLDOWN
        self.assertFalse(engine._on_cooldown(job))

    def test_worker_and_invalid_enqueue(self):
        resolver = Mock()
        resolver.query_multiple.return_value = None
        engine, _ = self.engine(resolver=resolver)
        engine.resolve_domain(0, 'host')
        engine.resolve_domain(1, '')
        engine.resolve_domain(1, 'host')
        engine.resolve_domain(1, 'host')
        engine.jobs.join()
        resolver.query_multiple.assert_called_once_with('host')
        engine.close()
        engine.close()
        engine.resolve_domain(2, 'host')
        self.assertTrue(engine.jobs.empty())

    def test_full_queue_is_dropped(self):
        engine, _ = self.engine()
        engine.jobs = Mock()
        from queue import Full
        engine.jobs.put_nowait.side_effect = Full
        with self.assertLogs('artex.enrich', level='WARNING'):
            engine.resolve_domain(1, 'host')

    @patch('artex.enrich.build_opener')
    def test_client_proxy_is_dynamic_direct_is_explicit_and_redirects_disabled(self, build):
        proxy = Mock(side_effect=['http://localhost:8080', ''])
        client = HTTPClient(proxy)
        client.get('https://example.test')
        self.assertEqual(build.call_args.args[0].proxies['https'], 'http://localhost:8080')
        opener = build.return_value
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('User-agent'), 'artex-enrich/1.0')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 12.0)
        client.get('https://example.test')
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))

    @patch('artex.enrich.build_opener')
    def test_client_http_errors_are_responses(self, build):
        response = HTTPError('https://example.test', 302, 'Found', {}, io.BytesIO(b''))
        self.addCleanup(response.close)
        build.return_value.open.side_effect = response
        self.assertIs(HTTPClient().get('https://example.test'), response)


if __name__ == '__main__':
    unittest.main()
