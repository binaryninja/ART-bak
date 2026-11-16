import unittest
from unittest.mock import patch
import test_skill_file_listing as listing
from artex.server.core import Request,json_response
from artex.server.skill_files_read import SkillFilesReadService,parse_request

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=listing.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        (self.f.skill/'secret-name').write_text('CONTENT NEVER READ')
        self.allowed=True;self.names=[];self.reader=self.f.make()
        self.service=SkillFilesReadService(self.reader,authorize=lambda r,s,n:self.names.append(n) or self.allowed)
    def request(self,path='/api/skills/sample/files',**kwargs):return Request('GET',path,{},**kwargs)
    def test_names_only(self):
        response=self.service.handle(self.request())
        self.assertEqual(response.status,200);self.assertEqual(response.json(),{'files':['secret-name']})
        self.assertNotIn(b'CONTENT',response.body);self.assertTrue(all(n=='sample' for n in self.names))
    def test_invalid_targets(self):
        for target in ('/api/skills/UPPER/files','/api/skills/a%2Fb/files','/api/skills/%FF/files','/api/skills/../files','/api/skills/sample/files?x=1','https://example.com/api/skills/sample/files','/api/skills/sample/files#x'):
            self.assertEqual(self.service.handle(self.request(target)).status,400)
        self.assertEqual(self.service.handle(self.request(body=b'{}')).status,400)
    def test_no_fs_before_grants(self):
        self.allowed=False
        with patch('artex.skills.file_listing.ScopedFilesystem') as fs:
            self.assertEqual(self.service.handle(self.request()).status,403);fs.assert_not_called()
    def test_missing_skill_sanitized(self):
        response=self.service.handle(self.request('/api/skills/absent/files'))
        self.assertEqual(response.status,404);self.assertNotIn(str(self.f.root).encode(),response.body)
    def test_permission_and_os_errors_sanitized(self):
        for error,status in ((PermissionError('PRIVATE PATH'),403),(OSError('PRIVATE PATH'),503)):
            with patch('artex.skills.file_listing.ScopedFilesystem',side_effect=error):
                response=self.service.handle(self.request())
            self.assertEqual(response.status,status);self.assertNotIn(b'PRIVATE PATH',response.body)
    def test_serialization_revocation(self):
        def revoke(status,value):
            response=json_response(status,value)
            if status==200:self.f.allowed=False
            return response
        with patch('artex.server.skill_files_read.json_response',revoke):response=self.service.handle(self.request())
        self.assertEqual(response.status,403);self.assertNotIn(b'secret-name',response.body)
    def test_root_replaced_during_serialization(self):
        moved=self.f.root.with_name(self.f.root.name+'-old')
        self.addCleanup(lambda:__import__('shutil').rmtree(moved,ignore_errors=True))
        def replace(status,value):
            response=json_response(status,value)
            if status==200:self.f.root.rename(moved);self.f.root.mkdir()
            return response
        with patch('artex.server.skill_files_read.json_response',replace):response=self.service.handle(self.request())
        self.assertEqual(response.status,409);self.assertNotIn(b'secret-name',response.body)

if __name__=='__main__':unittest.main()
