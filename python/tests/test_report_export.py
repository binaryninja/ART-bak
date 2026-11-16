import unittest,json,hashlib,zipfile,io
from datetime import datetime
from dataclasses import replace
from artex.report import Finding,TrafficBinding
from artex.report_export import ExportEvidence,findings_evidence_zip
class Tests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,1,1)
        self.binding=TrafficBinding(7,role='proof',method='GET',url='javascript:alert(1)',status=200,note='raw note')
        self.f=Finding(5,self.now,name='../Title',summary='<script>raw</script>',traffic_bindings=[self.binding])
        snap=dict(id='snapshot',method='GET',url=self.binding.url,status=200,req_head='GET / HTTP/1.1\r\nX: raw\r\n\r\n',resp_head='HTTP/1.1 200 OK\n',req_len=2,resp_len=3,req_hash=hashlib.sha256(b'\0x').hexdigest(),resp_hash=hashlib.sha256(b'\xff\0y').hexdigest())
        self.manifest=dict(id='7',finding_id='5',snapshot_id='snapshot',role='proof',note='raw note',position=0,created_at='2026-01-01T00:00:00Z',snapshot=snap)
        self.e=ExportEvidence(5,7,json.dumps(self.manifest).encode(),b'\0x',b'\xff\0y')
    def export(self,**kw):return findings_evidence_zip([self.f],[self.e],self.now,**kw)
    def test_source_layout_binary_heads_and_determinism(self):
        data=self.export();self.assertEqual(data,self.export())
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(len(z.namelist()),6)
            self.assertEqual(z.read('evidence/5/7/request.http'),b'GET / HTTP/1.1\r\nX: raw\n\n\0x')
            self.assertEqual(z.read('evidence/5/7/response.bin'),b'\xff\0y')
            self.assertEqual(json.loads(z.read('evidence/5/7/manifest.json')),self.manifest)
            text=z.read(z.namelist()[0]).decode()
            self.assertIn('<script>raw</script>',text) # export text, NOT sanitized HTML
            self.assertIn('`GET javascript:alert(1)`',text)
            self.assertNotIn('[javascript:',text)
            self.assertIsNone(z.testzip())
    def test_missing_duplicate_and_foreign_ids_fail(self):
        for fs,es in (([self.f],[]),([self.f,self.f],[self.e,self.e]),([self.f],[replace(self.e,finding_id=6)])):
            with self.subTest(fs=len(fs),es=len(es)),self.assertRaises(ValueError):findings_evidence_zip(fs,es,self.now)
        self.f.id=True
        with self.assertRaises(ValueError):self.export()
    def test_body_truncation_hash_and_manifest_id_fail(self):
        for e in (replace(self.e,request_body=b'\0'),replace(self.e,response_body=b'xxx')):
            with self.assertRaises(ValueError):findings_evidence_zip([self.f],[e],self.now)
        self.manifest['id']=7;self.e=replace(self.e,manifest_json=json.dumps(self.manifest).encode())
        with self.assertRaises(ValueError):self.export()
    def test_duplicate_json_keys_and_oversized_archive(self):
        self.e=replace(self.e,manifest_json=b'{"id":"7","id":"7"}')
        with self.assertRaises(ValueError):self.export()
        self.setUp()
        with self.assertRaises(ValueError):self.export(max_entries=5)
        self.f.report='x'*2000
        with self.assertRaises(ValueError):self.export(max_bytes=1024)
    def test_safe_names_keep_ids_even_with_same_titles(self):
        f=Finding(6,self.now,name=self.f.name)
        data=findings_evidence_zip([self.f,f],[self.e],self.now)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names=z.namelist();self.assertEqual(len(names),len(set(names)))
            self.assertFalse(any(n.startswith('/') or '..' in n.split('/') or '\\' in n for n in names))
            self.assertTrue(any(n.endswith('_5.md') for n in names));self.assertTrue(any(n.endswith('_6.md') for n in names))
    def test_empty_archive_and_caller_order_preserved(self):
        data=findings_evidence_zip([],[],self.now)
        with zipfile.ZipFile(io.BytesIO(data)) as z:self.assertEqual(z.namelist(),[])
        low=Finding(2,self.now,severity='low');high=Finding(1,self.now,severity='high')
        with zipfile.ZipFile(io.BytesIO(findings_evidence_zip([low,high],[],self.now))) as z:
            self.assertTrue(z.namelist()[0].startswith('low_'))
