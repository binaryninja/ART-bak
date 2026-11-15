import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from artex.traffic import Traffic, PageQuery, MAX_INLINE_BODY, BLOB_PREVIEW, is_binary_body, normalize_search_host, validate_proxy_url, template_path


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Traffic(Path(self.tmp.name)/'store')
        self.addCleanup(self.t.close)

    def record(self, **kwargs):
        return self.t.record(url=kwargs.pop('url','https://example.test/items/123'), **kwargs)

    def test_helpers(self):
        self.assertEqual(normalize_search_host('https://EXAMPLE.test:443/a'), ('example.test','443'))
        self.assertEqual(normalize_search_host('[::1]:8080'), ('::1','8080'))
        self.assertEqual(normalize_search_host('::1'), ('::1',''))
        for host in ('', 'example.test:0', 'example.test:65536'):
            with self.assertRaises(ValueError): normalize_search_host(host)
        self.assertEqual(template_path('/x/123/0123456789abcdef'), '/x/{id}/{hex}')
        self.assertTrue(is_binary_body('IMAGE/PNG; x=1', b'x'))
        self.assertTrue(is_binary_body('text/plain', b'a\0b'))
        self.assertFalse(is_binary_body('application/sql', b'SELECT 1'))
        self.assertEqual(validate_proxy_url(' socks5://localhost:1 ').scheme, 'socks5')
        with self.assertRaises(ValueError): validate_proxy_url('file:///tmp/x')

    def test_store_get_metadata_and_reopen(self):
        ident = self.record(req_head='GET / HTTP/1.1\n', req_body=b'hello', resp_body=b'world')
        self.assertEqual(self.t.get(ident), ('GET / HTTP/1.1\n\nhello','\nworld'))
        self.assertEqual(self.t.count(),1)
        self.assertNotIn('req_body', self.t.search()[0])
        self.assertEqual(self.t.hosts(), [{'host':'example.test','count':1}])
        other=Traffic(self.t.directory)
        try: self.assertEqual(other.get(ident),self.t.get(ident))
        finally: other.close()
        with self.assertRaises(KeyError): self.t.get('missing')

    def test_spill_dedup_range_and_evidence(self):
        body = b'a'*(MAX_INLINE_BODY+1)
        ident = self.record(resp_body=body)
        self.record(resp_body=body)
        digest = hashlib.sha256(body).hexdigest()
        self.assertEqual(len(list(self.t.directory.glob('_blobs/**/*.bin'))),1)
        self.assertIn('@blob sha256:'+digest,self.t.get(ident)[1])
        self.assertEqual(self.t.blob_range(digest,2,3),(b'aaa',len(body)))
        self.assertEqual(self.t.blob_range(digest,len(body)+1),(b'',len(body)))
        self.assertEqual(self.t.blob_range(digest,-1,2),(b'aa',len(body)))
        evidence=[]
        self.t.read_evidence([ident], lambda e: evidence.append((e['response'].read(), e['response'])))
        self.assertEqual(evidence[0][0],body)
        self.assertTrue(evidence[0][1].closed)
        with self.assertRaises(ValueError): self.t.blob_path('../bad')

    def test_binary_and_utf8_preview(self):
        binary=b'\x89PNG'+b'\0'*MAX_INLINE_BODY
        ident=self.record(resp_body=binary,content_type='image/png')
        self.assertIn('[binary image/png',self.t.get(ident)[1])
        text=('\u20ac'*100000).encode()
        ident=self.record(resp_body=text)
        stored=self.t.db.execute('SELECT resp_body FROM exchange_bodies WHERE id=?',(ident,)).fetchone()[0]
        self.assertLessEqual(len(stored),BLOB_PREVIEW)
        stored.decode('utf8')

    def test_query_ports_and_page_filters(self):
        self.record(url='http://example.test:8080/a',status=404, timestamp=1,resp_body=b'123')
        self.record(url='http://example.test:9090/a',status=201, timestamp=2,method='POST')
        self.assertEqual(len(self.t.query('example.test:8080')),1)
        rows,total=self.t.page(PageQuery(status='4xx',method='get',resp_min=2,sort='status;DROP TABLE exchanges'))
        self.assertEqual(total,1); self.assertEqual(rows[0]['status'],404)
        self.assertEqual(self.t.count(),2)
        self.assertEqual(self.t.page(PageQuery(query='9090'))[1],1)
        with self.assertRaises(ValueError): self.t.query('example.test',body_contains='ab')

    def test_archive_idempotence_and_hot_rows_win(self):
        body=b'long'*MAX_INLINE_BODY
        self.record(exchange_id='one',resp_body=body)
        archive=Path(self.tmp.name)/'archive'
        self.assertEqual(self.t.export_hosts([' EXAMPLE.test ','example.test'], archive),1)
        snapshot=json.loads((archive/'traffic.json').read_text())
        self.assertEqual(snapshot['version'],1)
        dest=Traffic(Path(self.tmp.name)/'restore')
        try:
            self.assertEqual(dest.import_archive(archive),1)
            self.assertEqual(dest.get('one'),self.t.get('one'))
            self.assertEqual(dest.import_archive(archive),0)
            dest.record(exchange_id='one',url='https://example.test/hot',resp_body=b'hot')
            self.assertEqual(dest.import_archive(archive),0)
            self.assertEqual(dest.get('one')[1],'\nhot')
        finally: dest.close()
        digest=snapshot['blobs'][0]
        (archive/'blobs'/(digest+'.bin')).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'checksum'): self.t.import_archive(archive)

    def test_legacy_evidence_and_path_safety(self):
        ident=self.record()
        p=self.t.directory/'legacy'
        p.mkdir(); (p/'request.http').write_bytes(b'GET / HTTP/1.1\r\n\r\nhello'); (p/'response.http').write_bytes(b'HTTP 200\n\nworld')
        with self.t.db:
            self.t.db.execute('DELETE FROM exchange_bodies WHERE id=?',(ident,))
            self.t.db.execute('UPDATE exchanges SET path=? WHERE id=?',('legacy',ident))
        items=[]
        self.t.read_evidence([ident], lambda e: items.append(e['request'].read()))
        self.assertEqual(items,[b'hello'])
        with self.t.db: self.t.db.execute('UPDATE exchanges SET path=? WHERE id=?',('../outside',ident))
        with self.assertRaises(ValueError): self.t.get(ident)

    def test_body_search_explicit_degradation(self):
        self.record(resp_body=b'unique-marker')
        if self.t.fts:
            self.assertEqual(len(self.t.query('example.test',body_contains='unique-marker')),1)
        else:
            with self.assertRaises(ValueError): self.t.query('example.test',body_contains='unique-marker')
        self.assertEqual(self.t.page(PageQuery(body='ab'))[1],1)
        self.t.fts = False
        with self.assertRaises(ValueError): self.t.query('example.test',body_contains='unique-marker')

if __name__=='__main__': unittest.main()
