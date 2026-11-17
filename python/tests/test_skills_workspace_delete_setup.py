"""Independent optional delete composition; synthetic private trees only."""
import unittest
from unittest.mock import patch

from artex.server.skill_path_delete import SkillPathDeleteService
from artex.server.skill_create import SkillCreateService
from artex.skills.file_listing import SkillDirectoryScope
import test_skills_workspace_setup as fixtures


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.allowed=True

    def service(self,scope=None,guard=None):
        return SkillPathDeleteService(scope or self.f.scope,authorize=lambda *a:True,
            mutation_authorize=lambda *a:True,delete_authorize=lambda *a:self.allowed,
            current=lambda *a:self.f.open,writer_guard=guard or self.f.guard)

    def bind(self,**changes):
        self.delete=self.service()
        values={'delete_service':self.delete,'authorize_delete':lambda *a:True,**changes}
        self.bundle=self.f.bind(**values);self.f.app=self.bundle.application
        return self.bundle

    def test_default_route_stays_unsupported_and_optional_arguments_are_paired(self):
        self.f.login()
        self.assertEqual(self.f.request('/api/skills/sample/files/ordinary','DELETE')[0],405)
        with self.assertRaises(ValueError):self.f.bind(delete_service=self.service())
        with self.assertRaises(ValueError):self.f.bind(authorize_delete=lambda *a:True)

    def test_delete_only_extension_does_not_accidentally_enable_root_creation(self):
        bundle=self.bind();self.f.login()
        self.assertIsNone(bundle.create);self.assertIs(bundle.delete,self.delete)
        self.assertEqual(self.f.request('/api/skills','POST',{'name':'new','description':'Synthetic'})[0],405)
        path=self.f.root/'sample'/'temporary';path.write_text('Synthetic')
        self.assertEqual(self.f.request('/api/skills/sample/files/temporary','DELETE')[:2],(200,{'deleted':'temporary'}))
        self.assertFalse(path.exists())
        self.assertEqual(self.f.request('/api/skills/sample/files/SKILL.md')[0],200)

    def test_both_extensions_route_create_and_delete_independently(self):
        create=SkillCreateService(self.f.scope,authorize=lambda *a:True,mutation_authorize=lambda *a:True,
                                 current=lambda *a:self.f.open,writer_guard=self.f.guard)
        self.bind(create_service=create,authorize_create=lambda *a:True);self.f.login()
        self.assertEqual(self.f.request('/api/skills','POST',{'name':'new','description':'Synthetic'})[:2],(201,{'name':'new'}))
        self.assertEqual(self.f.request('/api/skills/new/files/test','PUT',{'content':'Synthetic'})[0],200)
        self.assertEqual(self.f.request('/api/skills/new/files/test','DELETE')[:2],(200,{'deleted':'test'}))
        status,_,headers=self.f.request('/api/skills/new/files/test','PATCH')
        self.assertEqual((status,headers['Allow']),(405,'GET, HEAD, PUT, DELETE'))

    def test_grants_are_independent_and_original_base_is_untouched(self):
        self.bind();self.f.login()
        self.assertFalse(any('/skills' in row[1] for row in self.f.original.router._routes))
        path=self.f.root/'sample'/'kept';path.write_text('Synthetic')
        self.allowed=False
        self.assertEqual(self.f.request('/api/skills/sample/files/kept','DELETE')[0],403)
        self.assertTrue(path.exists())
        self.assertEqual(self.f.request('/api/skills/sample/files/kept','PUT',{'content':'Still allowed'})[0],200)
        self.allowed=True
        self.bind(authorize_delete=lambda *a:False);self.f.login()
        self.assertEqual(self.f.request('/api/skills/sample/files/kept','DELETE')[0],403)
        self.assertEqual(path.read_text(),'Still allowed')

    def test_shared_root_and_same_guard_required_without_filesystem_effects(self):
        foreign=SkillDirectoryScope(self.f.scope.root,self.f.scope.device,self.f.scope.inode,'foreign')
        with self.assertRaises(PermissionError):self.bind(delete_service=self.service(foreign))
        with self.assertRaises(PermissionError):self.bind(delete_service=self.service(guard=lambda *a:self.f.guard()))
        with patch('artex.tools.filesystem.ScopedFilesystem.__init__',side_effect=AssertionError('No IO during composition')):
            self.bind()

    def test_retargeted_delete_dispatch_fails_closed_and_never_runs_another_family(self):
        self.bind();self.f.login()
        path=self.f.root/'sample'/'kept';path.write_text('Synthetic')
        self.f.app.delete_view=self.f.app.views[3]
        self.assertEqual(self.f.request('/api/skills/sample/files/kept','DELETE')[0],503)
        self.assertEqual(path.read_text(),'Synthetic')


if __name__=='__main__':unittest.main()
