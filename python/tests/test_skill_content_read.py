import unittest
from unittest.mock import patch
import test_skill_file_read as source
from artex.server.core import Request,json_response
from artex.server.skill_content_read import SkillContentReadService

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=source.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.allowed=True;self.selections=[];self.reader=self.f.reader()
        self.service=SkillContentReadService(self.reader,authorize=lambda r,s,q:self.selections.append(q) or self.allowed)
    def request(self,path='/api/skills/sample/files/SKILL.md',**kwargs):return Request('GET',path,{},**kwargs)
    def test_content_dto_same_selection(self):
        response=self.service.handle(self.request());self.assertEqual(response.status,200)
        self.assertEqual(response.json(),{'content':'inert private instructions','file':'SKILL.md'})
        self.assertEqual(len({id(q) for q in self.selections}),1)
    def test_nested_file(self):
        (self.f.f.skill/'refs').mkdir();(self.f.f.skill/'refs'/'example.txt').write_text('inert nested')
        response=self.service.handle(self.request('/api/skills/sample/files/refs/example.txt'))
        self.assertEqual(response.status,200);self.assertEqual(response.json()['file'],'refs/example.txt')
    def test_invalid_targets(self):
        for path in ('/api/skills/UPPER/files/SKILL.md','/api/skills/sample/files/../outside','/api/skills/sample/files/%2e%2e/outside','/api/skills/sample/files/a//b','/api/skills/sample/files/a/./b','/api/skills/sample/files/a?x=1','https://example.com/api/skills/sample/files/a','/api/skills/sample/files/%FF'):
            self.assertEqual(self.service.handle(self.request(path)).status,400)
    def test_no_open_before_request_grant(self):
        self.allowed=False
        with patch('artex.skills.file_read.ScopedFilesystem') as fs:
            self.assertEqual(self.service.handle(self.request()).status,403);fs.assert_not_called()
    def test_fs_error_sanitization(self):
        for error,status in ((FileNotFoundError('PRIVATE ROOT'),404),(PermissionError('PRIVATE ROOT'),403),(OSError('PRIVATE ROOT'),503)):
            with patch('artex.skills.file_read.ScopedFilesystem',side_effect=error):response=self.service.handle(self.request())
            self.assertEqual(response.status,status);self.assertNotIn(b'PRIVATE ROOT',response.body)
    def test_serialization_write_withholds_original_content(self):
        def write(status,value):
            response=json_response(status,value)
            if status==200:self.f.path.write_text('changed after serialization')
            return response
        with patch('artex.server.skill_content_read.json_response',write):response=self.service.handle(self.request())
        self.assertEqual(response.status,409);self.assertNotIn(b'private instructions',response.body)
    def test_serialization_revokes_grant(self):
        def revoke(status,value):
            response=json_response(status,value)
            if status==200:self.f.allowed=False
            return response
        with patch('artex.server.skill_content_read.json_response',revoke):response=self.service.handle(self.request())
        self.assertEqual(response.status,403);self.assertNotIn(b'private instructions',response.body)

if __name__=='__main__':unittest.main()
