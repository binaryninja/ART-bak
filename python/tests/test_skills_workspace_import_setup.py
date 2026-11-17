"""Independent explicit archive family composition, synthetic files only."""
import io
import json
import unittest
from unittest.mock import patch

from artex.server.skill_archive_import import SkillArchiveImportService
from artex.server.skill_create import SkillCreateService
from artex.server.skill_path_delete import SkillPathDeleteService
from artex.skills.file_listing import SkillDirectoryScope
import test_skills_workspace_setup as fixtures
from test_skills_workspace_import_host import archive,multipart


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.allowed=True

    def service(self,scope=None,guard=None):
        return SkillArchiveImportService(scope or self.f.scope,authorize=lambda *a:True,
            mutation_authorize=lambda *a:True,import_authorize=lambda *a:self.allowed,
            current=lambda *a:self.f.open,writer_guard=guard or self.f.guard)

    def bind(self,**changes):
        self.importer=self.service()
        self.bundle=self.f.bind(import_service=self.importer,
            authorize_import=changes.pop('authorize_import',lambda *a:True),**changes)
        self.f.app=self.bundle.application;return self.bundle

    def upload(self,name='imported',start=None):
        content_type,body=multipart(archive(name))
        env={'REQUEST_METHOD':'POST','PATH_INFO':'/api/skills/upload','CONTENT_TYPE':content_type,
            'CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body)}
        if self.f.token:env['HTTP_AUTHORIZATION']='Bearer '+self.f.token
        result=[];self.last_response=result
        def begin(status,headers):
            result.append((int(status.split()[0]),dict(headers)))
            if start:start()
        iterator=self.f.app(env,begin)
        try:raw=b''.join(iterator)
        finally:
            if callable(getattr(iterator,'close',None)):iterator.close()
        return result[0][0],json.loads(raw) if raw else None,result[0][1]

    def test_default_unchanged_and_optional_import_service_and_grant_are_paired(self):
        self.f.login();self.assertEqual(self.upload()[0],404)
        with self.assertRaises(ValueError):self.f.bind(import_service=self.service())
        with self.assertRaises(ValueError):self.f.bind(authorize_import=lambda *a:True)
        self.assertFalse((self.f.root/'imported').exists())

    def test_import_only_extension_never_enables_create_or_delete(self):
        bundle=self.bind();self.f.login()
        self.assertIs(bundle.importer,self.importer);self.assertIsNone(bundle.create);self.assertIsNone(bundle.delete)
        self.assertEqual(self.upload()[:2],(201,{'name':'imported','files':2}))
        self.assertEqual(self.f.request('/api/skills','POST',{'name':'other','description':'Synthetic'})[0],405)
        self.assertEqual(self.f.request('/api/skills/imported/files/refs/例.txt','DELETE')[0],405)
        for method in ('GET','HEAD','PUT','DELETE'):
            status,_,headers=self.f.request('/api/skills/upload',method)
            self.assertEqual((status,headers['Allow']),(405,'POST'))

    def test_all_three_optional_families_route_independently(self):
        create=SkillCreateService(self.f.scope,authorize=lambda *a:True,mutation_authorize=lambda *a:True,
            current=lambda *a:self.f.open,writer_guard=self.f.guard)
        delete=SkillPathDeleteService(self.f.scope,authorize=lambda *a:True,mutation_authorize=lambda *a:True,
            delete_authorize=lambda *a:True,current=lambda *a:self.f.open,writer_guard=self.f.guard)
        self.bind(create_service=create,authorize_create=lambda *a:True,delete_service=delete,authorize_delete=lambda *a:True)
        self.f.login();self.assertEqual(self.upload('upload')[:2],(201,{'name':'upload','files':2}))
        self.assertEqual(self.f.request('/api/skills','POST',{'name':'created','description':'Synthetic'})[0],201)
        self.assertEqual(self.f.request('/api/skills/upload/files/refs/例.txt','DELETE')[:2],(200,{'deleted':'refs/例.txt'}))
        self.assertTrue((self.f.root/'upload'/'SKILL.md').is_file());self.assertTrue((self.f.root/'created'/'SKILL.md').is_file())

    def test_import_service_and_global_grants_are_independent_from_write_access(self):
        self.bind();self.f.login();self.allowed=False
        self.assertEqual(self.upload()[0],403);self.assertFalse((self.f.root/'imported').exists())
        self.assertEqual(self.f.request('/api/skills/sample/files/still-allowed','PUT',{'content':'Synthetic'})[0],200)
        self.allowed=True;self.bind(authorize_import=lambda *a:False);self.f.login()
        self.assertEqual(self.upload()[0],403);self.assertFalse((self.f.root/'imported').exists())
        self.assertFalse(any('/skills' in row[1] for row in self.f.original.router._routes))

    def test_scope_and_guard_required_and_binding_has_no_filesystem_effects(self):
        foreign=SkillDirectoryScope(self.f.scope.root,self.f.scope.device,self.f.scope.inode,'foreign')
        with self.assertRaises(PermissionError):self.f.bind(import_service=self.service(foreign),authorize_import=lambda *a:True)
        with self.assertRaises(PermissionError):self.f.bind(import_service=self.service(guard=lambda *a:self.f.guard()),authorize_import=lambda *a:True)
        with patch('artex.tools.filesystem.ScopedFilesystem.__init__',side_effect=AssertionError('No IO during composition')):self.bind()

    def test_retargeted_import_view_fails_closed_without_dispatching_another_family(self):
        self.bind();self.f.login();self.f.app.import_view=self.f.app.views[3]
        self.assertEqual(self.upload()[0],503);self.assertFalse((self.f.root/'imported').exists())

    def test_post_header_import_grant_loss_withholds_receipt_without_rollback(self):
        self.bind();self.f.login()
        # The bare composer propagates the failed disclosure. Its containing
        # host, tested separately, owns quarantine and response-tail accounting.
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'):
            self.upload(start=lambda:setattr(self,'allowed',False))
        self.assertEqual(self.last_response[0][0],201)
        self.assertTrue((self.f.root/'imported'/'SKILL.md').is_file())


if __name__=='__main__':unittest.main()
