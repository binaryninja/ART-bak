"""Bounded synthetic ZIP planner tests; no archive is executed or extracted."""
import io
import struct
import unittest
import warnings
import zipfile
import zlib

from artex.skills.archive_plan import plan_archive, ArchiveLimit, MAX_ARCHIVE_BYTES, MAX_FILE_BYTES, MAX_TOTAL_BYTES

MD = b'---\nname: imported-skill\ndescription: Synthetic fixture\n---\nInert instructions.\n'


def archive(entries=None, *, method=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf,'w',compression=method) as z:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            for name,data in entries if entries is not None else [('SKILL.md',MD)]: z.writestr(name,data)
    return buf.getvalue()


def locations(data):
    return data.index(b'PK\x01\x02'),len(data)-22


def change(data, location, fmt, value):
    out = bytearray(data); struct.pack_into(fmt,out,location,value); return bytes(out)


class ArchivePlanTests(unittest.TestCase):
    def test_store_deflate_root_wrapper_and_inert_binary(self):
        for method in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
            for prefix in ('','wrapper/'):
                plan = plan_archive(archive([(prefix+'SKILL.md',MD),(prefix+'assets/',b''),(prefix+'assets/data.bin',b'\0\xff')],method=method))
                self.assertEqual(plan.name,'imported-skill'); self.assertEqual(plan.directories,('assets',))
                self.assertEqual([(f.path,f.data) for f in plan.files],[('SKILL.md',MD),('assets/data.bin',b'\0\xff')])
                self.assertEqual(plan.total_bytes,len(MD)+2)

    def test_unicode_flagged_names_and_metadata(self):
        plan = plan_archive(archive([('SKILL.md',MD.replace(b'imported-skill','測試'.encode())),('資料/文.bin',b'raw')]))
        self.assertEqual(plan.name,'測試'); self.assertEqual(plan.directories,('資料',))

    def test_path_traversal_aliases_controls_and_windows_ambiguities(self):
        for path in ('../x','/x','a/../x','a/./x','a//x','a\\x','x%2fy','./x','x\0y','x\ny','x?y','a../x','a./x','a /x','CON','aux.txt','LPT1.dat','e\u0301.txt'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                # zipfile truncates at NUL, so create a raw name substitution instead.
                data = archive([('SKILL.md',MD),(path.replace('\0','~'),b'raw')])
                if '\0' in path: data = data.replace(b'x~y',b'x\0y')
                plan_archive(data)

    def test_path_component_depth_and_total_bounds(self):
        for path in ('a'*256,'/'.join(['x']*33),'/'.join(['a'*255]*3)):
            with self.subTest(path=path), self.assertRaises(ValueError): plan_archive(archive([('SKILL.md',MD),(path,b'')]))

    def test_duplicate_casefold_normalization_and_prefix_collisions(self):
        for extra in ([('x',b'a'),('x',b'b')],[('Dir/a',b''),('dir/b',b'')],[('ß',b''),('ss',b'')],
                      [('x',b''),('x/y',b'')],[('x/',b''),('x',b'')]):
            with self.subTest(extra=extra), self.assertRaises(ValueError): plan_archive(archive([('SKILL.md',MD)]+extra))

    def test_exact_one_manifest_unambiguous_single_root(self):
        for entries in ([],[('x',b'')],[('skill.md',MD)],[('a/b/SKILL.md',MD)],
                        [('SKILL.md',MD),('a/SKILL.md',MD)],[('a/SKILL.md',MD),('outside.txt',b'')],
                        [('a/SKILL.md',MD),('b/',b'')],[('SKILL.md/',b'')]):
            with self.subTest(entries=entries), self.assertRaises(ValueError): plan_archive(archive(entries))

    def test_explicit_parseable_manifest_name_description_no_mcp(self):
        for content in (b'not frontmatter',b'---\ndescription: x\n---',b'---\nname: x\n---',
                        MD.replace(b'imported-skill',b'bad.name'),MD.replace(b'Synthetic fixture',b' '),
                        MD.replace(b'---\nInert',b'mcps: example\n---\nInert'),MD+b'\0',MD+b'\xff',
                        MD.replace(b'Synthetic fixture',b'"\\ud800"'),MD.replace(b'Synthetic fixture',b'[nested]'),
                        MD.replace(b'name: imported-skill',b'name: imported-skill\nname: other')):
            with self.subTest(content=content), self.assertRaises(ValueError): plan_archive(archive([('SKILL.md',content)]))

    def test_resource_limits_include_implicit_directories(self):
        cases = [archive([('SKILL.md',MD)]+[(f'x{i}',b'') for i in range(128)]),
                 archive([('SKILL.md',MD),('large',b'x'*(MAX_FILE_BYTES+1))]),
                 archive([('SKILL.md',MD)]+[(f'x{i}',b'x'*MAX_FILE_BYTES) for i in range(4)]),
                 archive([('SKILL.md',MD)]+[(f'a{i}/b/c/d/e/f/g/h',b'') for i in range(17)])]
        for data in cases:
            with self.subTest(length=len(data)),self.assertRaises(ArchiveLimit):plan_archive(data)
        with self.assertRaises(ArchiveLimit):plan_archive(b'x'*(MAX_ARCHIVE_BYTES+1))

    def test_every_member_not_just_manifest_is_read_and_crc_checked(self):
        data = archive([('SKILL.md',MD),('data.bin',b'second-content')],method=zipfile.ZIP_STORED)
        with self.assertRaises(ValueError): plan_archive(data.replace(b'second-content',b'Second-content'))

    def test_understated_actual_size_with_crc_of_prefix_is_rejected(self):
        for method in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
            data = archive(method=method); central,_ = locations(data)
            for pos,fmt,value in ((14,'I',zlib.crc32(MD[:4])),(22,'I',4),
                                  (central+16,'I',zlib.crc32(MD[:4])),(central+24,'I',4)):
                data = change(data,pos,'<'+fmt,value)
            with self.assertRaises(ValueError): plan_archive(data)

    def test_false_declared_size_crc_and_local_header_mismatches(self):
        original = archive(); central,end = locations(original)
        for offset,fmt,value in ((14,'<I',0),(22,'<I',1),(central+16,'<I',0),(central+24,'<I',1),
                                 (6,'<H',0x800),(8,'<H',0),(central+42,'<I',1),(end+16,'<I',0)):
            with self.subTest(offset=offset),self.assertRaises(ValueError):plan_archive(change(original,offset,fmt,value))

    def test_unsupported_flags_versions_methods_metadata_and_disk(self):
        original = archive(); central,end = locations(original)
        for offset,fmt,value in ((central+8,'<H',1),(central+8,'<H',8),(central+8,'<H',2),
                                 (central+10,'<H',12),(central+6,'<H',45),(central+30,'<H',1),
                                 (central+32,'<H',1),(central+34,'<H',1),(central+36,'<H',2),
                                 (end+4,'<H',1),(end+6,'<H',1),(end+8,'<H',0),(end+20,'<H',1)):
            with self.subTest(offset=offset,value=value),self.assertRaises(ValueError):plan_archive(change(original,offset,fmt,value))

    def test_link_special_attributes_and_directory_payload_refused(self):
        original = archive(); central,_ = locations(original)
        for mode in (0o120777,0o020600,0o010600,0o140600,0o104600,0o040700):
            with self.subTest(mode=mode),self.assertRaises(ValueError):plan_archive(change(original,central+38,'<I',mode<<16))
        with self.assertRaises(ValueError):plan_archive(archive([('SKILL.md',MD),('dir/',b'data')]))

    def test_truncation_preamble_trailer_and_bad_compressed_stream(self):
        data = archive()
        for bad in (data[:-1],b'preamble'+data,data+b'trailer',b'',data[:15],data[:30]):
            with self.subTest(length=len(bad)),self.assertRaises(ValueError):plan_archive(bad)
        raw = bytearray(data); start=30+len('SKILL.md'); raw[start:start+3]=b'\xff\xff\xff'
        with self.assertRaises(ValueError):plan_archive(bytes(raw))

    def test_deflate_trailing_second_stream_is_not_accepted(self):
        original = archive(); central,end = locations(original)
        compressed = struct.unpack_from('<I',original,18)[0]
        addition = b'\x03\x00'
        data = original[:central]+addition+original[central:]
        data = change(data,18,'<I',compressed+len(addition))
        data = change(data,central+len(addition)+20,'<I',compressed+len(addition))
        data = change(data,end+len(addition)+16,'<I',central+len(addition))
        with self.assertRaises(ValueError):plan_archive(data)

    def test_legacy_non_ascii_filename_without_utf8_flag_refused(self):
        data = archive([('SKILL.md',MD),('é',b'')]); out=bytearray(data)
        for signature,delta in ((b'PK\x03\x04',6),(b'PK\x01\x02',8)):
            pos=out.find(signature)
            while pos>=0:
                struct.pack_into('<H',out,pos+delta,0);pos=out.find(signature,pos+4)
        with self.assertRaises(ValueError):plan_archive(bytes(out))

    def test_declared_small_deflate_bomb_is_bounded_on_actual_output(self):
        raw=b'x'*(MAX_FILE_BYTES+100)
        data=archive([('SKILL.md',MD),('bomb',raw)]);out=bytearray(data)
        for signature,size_delta,crc_delta,name_delta,header_len in ((b'PK\x03\x04',22,14,26,30),(b'PK\x01\x02',24,16,28,46)):
            pos=out.find(signature)
            while pos>=0:
                nlen=struct.unpack_from('<H',out,pos+name_delta)[0]
                if bytes(out[pos+header_len:pos+header_len+nlen])==b'bomb':
                    struct.pack_into('<I',out,pos+size_delta,1);struct.pack_into('<I',out,pos+crc_delta,zlib.crc32(b'x'))
                pos=out.find(signature,pos+4)
        with self.assertRaises(ArchiveLimit):plan_archive(bytes(out))

    def test_maximum_exact_total_and_single_file_bounds_are_accepted(self):
        entries=[('SKILL.md',MD)]+[(f'x{i}',b'x'*MAX_FILE_BYTES) for i in range(3)]
        entries.append(('last',b'x'*(MAX_TOTAL_BYTES-3*MAX_FILE_BYTES-len(MD))))
        plan=plan_archive(archive(entries))
        self.assertEqual(plan.total_bytes,MAX_TOTAL_BYTES)
