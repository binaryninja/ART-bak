"""Opaque raw-setting CAS token, never a digest of decoded tool names."""
import unittest
from artex.db.tool_intercept_config import tools_digest
import test_intercept_config_read as base

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=base.Tests();self.f.setUp()
    def test_same_decoded_list_distinct_raw_versions(self):
        f=self.f;seen=[]
        for raw in ('["Bash"]',' [ "Bash" ]','["\\u0042ash"]'):
            f.db.rows=[(raw,)];before=len(f.db.calls);value=f.get().json()
            self.assertEqual(value['enabled_tools'],['Bash'])
            self.assertEqual(value['version_digest'],tools_digest(raw))
            self.assertEqual(len(f.db.calls),before+1);seen.append(value['version_digest'])
        self.assertEqual(len(set(seen)),3)
    def test_missing_null_malformed_remain_distinct(self):
        f=self.f;seen=[]
        for raw in (None,'null','invalid','[]'):
            f.db.rows=[] if raw is None else [(raw,)]
            value=f.get().json();self.assertEqual(value['version_digest'],tools_digest(raw));seen.append(value['version_digest'])
        self.assertEqual(len(set(seen)),4)
    def test_one_snapshot_and_legacy_read_compatibility(self):
        f=self.f;f.db.rows=[('["Bash"]',)]
        original=f.db.fetch_all
        def once(q):
            rows=original(q);f.db.rows=[('["other"]',)];return rows
        f.db.fetch_all=once
        value=f.get().json()
        self.assertEqual(value,{'enabled_tools':['Bash'],'version_digest':tools_digest('["Bash"]')})
        self.assertEqual(len(f.db.calls),1)
        self.assertEqual(f.reader.read(),['other'])
    def test_revoked_or_oversized_never_exposes_digest(self):
        f=self.f;f.db.rows=[('["Bash"]',)];f.db.hook=lambda:setattr(f,'allowed',False)
        self.assertNotIn(b'version_digest',f.get().body)
        f.allowed=True;f.db.hook=None;f.db.rows=[(None,)]
        self.assertEqual(f.get().status,500);self.assertNotIn(b'version_digest',f.get().body)
