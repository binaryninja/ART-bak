import hashlib,io,json,tarfile,tempfile,unittest
from pathlib import Path
try:
    import zstandard
except ImportError:
    raise unittest.SkipTest("Optional archive extra (zstandard) is not installed")
from artex.archive_bundle import build_bundle,read_bundle,publish_bundle,BundleInvalid

class BundleTests(unittest.TestCase):
    def manifest(self,version=3):return {'format_version':version,'created_at':'2026-11-15T00:00:00Z','task_id':1,'exploration_id':2,'source_task_ids':[],'hosts':[],'exclusive_hosts':[],'exclusive_asset_ids':[],'tables':{'tasks':[{'id':1,'future':'kept'}]},'data_counts':{},'aggregate_stats':{},'future_field':{'x':1}}
    def raw(self,entries):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w') as tar:
            for name,raw,type in entries:
                info=tarfile.TarInfo(name);info.type=type;info.size=len(raw);info.linkname='outside'
                tar.addfile(info,io.BytesIO(raw))
        return zstandard.ZstdCompressor(write_checksum=True).compress(out.getvalue())
    def test_source_format_constants(self):
        root=Path(__file__).resolve().parents[2]
        go=(root/'db/task_archives.go').read_text()
        self.assertIn('TaskArchiveFormatVersion       = 3',go);self.assertIn('"database/llm_records.ndjson"',go)
        self.assertIn('hash[:2], hash+".bin"',(root/'evidence/store.go').read_text())
        self.assertIn('zstd.EncoderLevelFromZstd(10)',(root/'server/task_archive_package.go').read_text())
    def test_roundtrip_layout_unknown_fields_and_checksum(self):
        files={'files/tasks/1/result.txt':b'result','files/transcripts/exp2-main.jsonl':b'{}\n'}
        data=build_bundle(self.manifest(),files);digest=hashlib.sha256(data).hexdigest()
        result=read_bundle(data,expected_sha=digest.upper())
        self.assertEqual(result.files,files);self.assertEqual(result.manifest['future_field'],{'x':1});self.assertEqual(result.compressed_size,len(data))
        raw=zstandard.ZstdDecompressor().decompress(data)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:self.assertIn('manifest.json',tar.getnames())
        with self.assertRaises(BundleInvalid):read_bundle(data,expected_sha='0'*64)
    def test_streamed_rows_versions_and_count(self):
        manifest=self.manifest(2);manifest['streamed_tables']={'llm_records':'database/llm_records.ndjson'};manifest['data_counts']={'llm_records':2}
        files={'database/llm_records.ndjson':b'{"id":1}\n{"id":2}\n'}
        self.assertEqual(read_bundle(build_bundle(manifest,files)).manifest['format_version'],2)
        manifest['data_counts']['llm_records']=3
        with self.assertRaises(BundleInvalid):build_bundle(manifest,files)
        manifest['format_version']=1
        with self.assertRaises(BundleInvalid):build_bundle(manifest,files)
    def test_evidence_hash_length_path(self):
        body=b'fixture';digest=hashlib.sha256(body).hexdigest();path=f'evidence/blobs/{digest[:2]}/{digest}.bin'
        manifest=self.manifest();manifest['tables']['traffic_evidence_snapshots']=[{'req_hash':digest,'resp_hash':digest,'req_len':len(body),'resp_len':len(body)}]
        self.assertEqual(read_bundle(build_bundle(manifest,{path:body})).files[path],body)
        with self.assertRaises(BundleInvalid):build_bundle(manifest,{path:b'bad'})
        with self.assertRaises(BundleInvalid):build_bundle(manifest,{})
    def test_malicious_types_and_paths(self):
        for path,type in (('../x',tarfile.REGTYPE),('/tmp/x',tarfile.REGTYPE),('files/x',tarfile.SYMTYPE),('files/x',tarfile.LNKTYPE),('files/x',tarfile.FIFOTYPE),('a\\b',tarfile.REGTYPE)):
            with self.assertRaises(BundleInvalid):read_bundle(self.raw([(path,b'',type)]))
    def test_duplicate_and_parent_collision(self):
        for entries in ([('x',b'',tarfile.REGTYPE),('x',b'',tarfile.REGTYPE)],[('x',b'',tarfile.REGTYPE),('x/y',b'',tarfile.REGTYPE)]):
            with self.assertRaises(BundleInvalid):read_bundle(self.raw(entries))
    def test_expansion_bounds_and_corruption(self):
        data=build_bundle(self.manifest(),{'files/tasks/1/big':b'0'*20000})
        with self.assertRaises(BundleInvalid):read_bundle(data,max_bytes=15000)
        with self.assertRaises(BundleInvalid):read_bundle(data,max_entries=1)
        with self.assertRaises(BundleInvalid):read_bundle(b'invalid')
        with self.assertRaises(BundleInvalid):build_bundle(self.manifest(),{},max_bytes=100)
    def test_scope_and_unreferenced_stream(self):
        for files in ({'files/tasks/2/x':b''},{'files/transcripts/exp3-main.jsonl':b''},{'database/llm_records.ndjson':b''}):
            with self.assertRaises(BundleInvalid):build_bundle(self.manifest(),files)
    def test_atomic_create_only_publish_and_symlinks(self):
        data=build_bundle(self.manifest(),{})
        with tempfile.TemporaryDirectory() as root:
            publish_bundle(root,'task-1-7.tar.zst',data)
            path=Path(root)/'task-1-7.tar.zst';self.assertEqual(path.read_bytes(),data);self.assertEqual(path.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):publish_bundle(root,path.name,data)
            link=Path(root)/'link';link.symlink_to(root,target_is_directory=True)
            with self.assertRaises(OSError):publish_bundle(link,'task-1-8.tar.zst',data)
            with self.assertRaises(BundleInvalid):publish_bundle(root,'task-2-8.tar.zst',data)
            self.assertEqual(list(Path(root).glob('.archive-*')),[])
    def test_optional_codec_missing_no_install(self):
        from unittest.mock import patch
        from artex.archive_bundle import BundleUnavailable
        with patch.dict('sys.modules',{'zstandard':None}):
            with self.assertRaises(BundleUnavailable):build_bundle(self.manifest(),{})
    def test_legacy_and_future_table_rows_preserved(self):
        for version in (1,2,3):
            manifest=self.manifest(version);manifest['tables']['future_table']=[{'unknown':{'k':'v'}}]
            result=read_bundle(build_bundle(manifest,{}))
            self.assertEqual(result.manifest['tables']['future_table'],[{'unknown':{'k':'v'}}])
        manifest=self.manifest(4)
        with self.assertRaises(BundleInvalid):build_bundle(manifest,{})
        manifest=self.manifest();manifest['tables']['tasks']={}
        with self.assertRaises(BundleInvalid):build_bundle(manifest,{})

    def test_decoder_window_bound_not_only_output_bound(self):
        # Frame declares256KiB window although decompressed payload is short.
        params=zstandard.ZstdCompressionParameters.from_level(1,window_log=18,write_content_size=0)
        data=zstandard.ZstdCompressor(compression_params=params).compress(b'x'*200000)
        self.assertGreater(zstandard.get_frame_parameters(data).window_size,65536)
        with self.assertRaises(BundleInvalid):read_bundle(data,max_bytes=65536)
        # Direct codec check pins observed0.25 byte units; KiB would admit it.
        with self.assertRaises(zstandard.ZstdError):
            with zstandard.ZstdDecompressor(max_window_size=65536).stream_reader(io.BytesIO(data)) as stream:stream.read(1)
    def test_malformed_counts_typed(self):
        manifest=self.manifest();manifest['data_counts']=[]
        with self.assertRaises(BundleInvalid):build_bundle(manifest,{})
