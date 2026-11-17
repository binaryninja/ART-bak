"""Inert private-root creation contracts; no provider, archive, MCP or SQL."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from artex.skills import parse_skill, render_skill
from artex.skills.file_listing import SkillDirectoryScope
from artex.server.auth import AuthService, sign_jwt
from artex.server.wsgi import WSGIApplication
from artex.server.skill_create import (SkillCreateService, bind_skill_creation,
    MAX_BYTES, MAX_METADATA_BYTES, MAX_JSON_BYTES)


class CreateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name); self.root = self.base/'root'; self.root.mkdir(mode=0o700)
        info = self.root.stat()
        self.scope = SkillDirectoryScope(str(self.root),info.st_dev,info.st_ino,'synthetic')
        self.grants = dict(current=True,source=True,mutation=True,global_=True)
        self.hooks = {}; self.counts = dict.fromkeys(self.grants,0); self.guard_exit = None
        self.lock = threading.RLock(); self.proposals = []
        def grant(key):
            def call(*args):
                self.counts[key] += 1
                if key == 'mutation': self.proposals.append(args[1])
                hook = self.hooks.get(key)
                if hook: hook(self.counts[key],*args)
                return self.grants[key]
            return call
        @contextmanager
        def guard(*args):
            with self.lock:
                yield
                if self.guard_exit: self.guard_exit()
        self.guard = guard
        self.service = SkillCreateService(self.scope,authorize=grant('source'),mutation_authorize=grant('mutation'),
                                           current=grant('current'),writer_guard=guard)
        self.auth = AuthService(Mock(),Mock(),b'synthetic-skill-create-key-only!!')
        self.original = WSGIApplication(self.auth.router(),max_body_bytes=MAX_JSON_BYTES)
        self.global_grant = grant('global_')
        self.app = bind_skill_creation(self.original,service=self.service,authorize_global=self.global_grant)
        self.token = sign_jwt(self.auth.key)

    def request(self,data=None,*,body=None,method='POST',path='/api/skills',auth=True,start=None):
        body = json.dumps({'name':'new-skill','description':'Synthetic 世界'} if data is None else data,
                          ensure_ascii=False).encode() if body is None else body
        env = dict(REQUEST_METHOD=method,PATH_INFO=path,CONTENT_LENGTH=str(len(body)),**{'wsgi.input':io.BytesIO(body)})
        if auth: env['HTTP_AUTHORIZATION'] = 'Bearer '+self.token
        result = []
        def begin(status,headers):
            result.append((int(status.split()[0]),dict(headers)))
            if start: start()
        iterator = self.app(env,begin)
        try: raw = b''.join(iterator)
        finally:
            if callable(getattr(iterator,'close',None)): iterator.close()
        self.assertEqual(result[0][1]['Content-Length'],str(len(raw)))
        return result[0][0],json.loads(raw) if raw else None

    def assert_unknown(self,result):
        self.assertEqual(result[0],500)
        self.assertEqual(result[1]['outcome'],'unknown')
        self.assertIs(result[1]['retry'],False)

    def test_minimal_create_is_inert_private_parseable_and_exact_receipt(self):
        self.assertEqual(self.request(),(201,{'name':'new-skill'}))
        directory = self.root/'new-skill'; path = directory/'SKILL.md'
        self.assertEqual(set(p.name for p in directory.iterdir()),{'SKILL.md'})
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        self.assertEqual(path.stat().st_nlink,1)
        skill = parse_skill(path.read_text())
        self.assertEqual((skill.name,skill.description),('new-skill','Synthetic 世界'))
        self.assertEqual(skill.mcps,())
        self.assertIn('Describe step-by-step instructions',skill.instructions)
        proposal = self.proposals[-1]
        self.assertIs(proposal.scope,self.scope)
        self.assertEqual(proposal.content_bytes,len(path.read_bytes()))
        self.assertEqual(proposal.parent_identity,(self.scope.device,self.scope.inode))

    def test_unicode_name_and_quoted_metadata_preserved_as_data(self):
        data = dict(name='測試',description='value: # not YAML syntax',license='"quoted"',
                    compatibility='plain compatibility',instructions='## Inert text\n\nNo execution.\n')
        self.assertEqual(self.request(data),(201,{'name':'測試'}))
        skill = parse_skill((self.root/'測試/SKILL.md').read_text())
        for key,value in data.items(): self.assertEqual(getattr(skill,key),value)

    def test_yaml_looking_metadata_round_trips_without_new_fields_or_mcps(self):
        for index,value in enumerate(('desc # comment', '"quoted"', "'quoted'", '[server]',
                                      '{mcps: server}', '*alias', '&anchor', '| literal', '---')):
            name = 'fixture-'+str(index)
            self.assertEqual(self.request(dict(name=name,description=value,license=value,compatibility=value)),
                             (201,{'name':name}))
            parsed = parse_skill((self.root/name/'SKILL.md').read_text())
            self.assertEqual((parsed.description,parsed.license,parsed.compatibility),(value,value,value))
            self.assertEqual(parsed.mcps,())
            self.assertEqual(set(parsed.metadata),{'name','description','license','compatibility'})

    def test_every_creation_grant_is_independent_and_denies_before_io(self):
        for key in self.grants:
            with self.subTest(key=key):
                self.grants[key] = False
                with patch('artex.server.skill_create.ScopedFilesystem',side_effect=AssertionError('No filesystem reads')):
                    self.assertEqual(self.request()[0],403)
                self.grants[key] = True
        self.assertEqual(list(self.root.iterdir()),[])

    def test_unauthorized_never_calls_creation_policies(self):
        self.assertEqual(self.request(auth=False)[0],401)
        self.assertEqual(self.counts,dict.fromkeys(self.grants,0))
        self.assertEqual(list(self.root.iterdir()),[])

    def test_invalid_fields_names_and_text_are_rejected_before_io(self):
        cases = ({}, {'name':'x'}, {'name':'x','description':''},
            {'name':'x','description':'ok','mcps':[]}, {'name':'x','description':'ok','visibility':{}},
            {'name':'x','description':'ok','license':None}, {'name':'x','description':'line\nbreak'},
            {'name':'x','description':'ok','instructions':'bad\0text'})
        cases += tuple({'name':name,'description':'ok'} for name in ('../outside','/outside','Bad','x--y','x-',
                                                                   'a'*65,'𐐀'*64))
        with patch('artex.server.skill_create.ScopedFilesystem',side_effect=AssertionError('No filesystem reads')):
            for data in cases:
                with self.subTest(data=data): self.assertEqual(self.request(data)[0],400)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_json_duplicates_alternate_encoding_and_depth_rejected(self):
        bodies = (b'{"name":"one","name":"two","description":"d"}',b'[]',
                  json.dumps({'name':'x','description':'d'}).encode('utf-16'),
                  b'\xef\xbb\xbf{"name":"x","description":"d"}',
                  b'['*12000+b'0'+b']'*12000)
        for body in bodies: self.assertEqual(self.request(body=body)[0],400)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_bounds_include_rendered_file_and_utf8_bytes(self):
        cases = ({'name':'x','description':'界'*(MAX_METADATA_BYTES//3+1)},
                 {'name':'x','description':'d','instructions':'a'*MAX_BYTES})
        for data in cases: self.assertEqual(self.request(data)[0],413)
        self.assertEqual(self.request(body=b' '*(MAX_JSON_BYTES+1))[0],413)
        self.assertEqual(list(self.root.iterdir()),[])
        prefix = len(render_skill('x','d',instructions='a').encode())-1
        self.assertEqual(self.request({'name':'x','description':'d','instructions':'a'*(MAX_BYTES-prefix)}),
                         (201,{'name':'x'}))
        self.assertEqual((self.root/'x/SKILL.md').stat().st_size,MAX_BYTES)

    def test_existing_directory_file_and_dangling_symlink_never_overwritten(self):
        outside = self.base/'outside'; outside.write_text('untouched')
        (self.root/'directory').mkdir(); (self.root/'directory/SKILL.md').write_text('existing')
        (self.root/'file').write_text('existing file')
        (self.root/'alias').symlink_to(outside)
        (self.root/'dangling').symlink_to(self.base/'missing')
        for name in ('directory','file','alias','dangling'):
            self.assertEqual(self.request({'name':name,'description':'d'})[0],409)
        self.assertEqual(outside.read_text(),'untouched')
        self.assertEqual((self.root/'directory/SKILL.md').read_text(),'existing')
        self.assertEqual((self.root/'file').read_text(),'existing file')

    def test_late_pre_mkdir_collision_is_detected_before_attempt(self):
        def collision(count,*args):
            if count == 3: (self.root/'new-skill').write_text('other operation')
        self.hooks['mutation'] = collision
        with patch('artex.server.skill_create.os.mkdir',wraps=os.mkdir) as mkdir:
            self.assertEqual(self.request()[0],409)
            mkdir.assert_not_called()
        self.assertEqual((self.root/'new-skill').read_text(),'other operation')

    def test_root_replacement_before_attempt_never_writes_either_root(self):
        def replace(count,*args):
            if count == 2:
                self.root.rename(self.base/'original-root'); self.root.mkdir(mode=0o700)
        self.hooks['mutation'] = replace
        self.assertEqual(self.request()[0],409)
        self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(list((self.base/'original-root').iterdir()),[])

    def test_new_directory_displacement_is_unknown_before_file_write(self):
        def replace(count,*args):
            if count == 4:
                (self.root/'new-skill').rename(self.root/'displaced')
                (self.root/'new-skill').mkdir()
        self.hooks['mutation'] = replace
        self.assert_unknown(self.request())
        self.assertEqual(list((self.root/'new-skill').iterdir()),[])
        self.assertEqual(list((self.root/'displaced').iterdir()),[])

    def test_revoke_after_mkdir_preserves_partial_directory_and_no_file(self):
        def revoke(count,*args):
            if count == 4: self.grants['mutation'] = False
        self.hooks['mutation'] = revoke
        self.assert_unknown(self.request())
        self.assertTrue((self.root/'new-skill').is_dir())
        self.assertEqual(list((self.root/'new-skill').iterdir()),[])

    def test_hardlink_substitution_before_first_write_cannot_change_other_file(self):
        outside = self.base/'outside'; outside.write_text('untouched')
        def replace(count,*args):
            if count == 5:
                path = self.root/'new-skill/SKILL.md'; path.unlink(); os.link(outside,path)
        self.hooks['mutation'] = replace
        self.assert_unknown(self.request())
        self.assertEqual(outside.read_text(),'untouched')

    def test_symlink_substitution_before_first_write_cannot_change_other_file(self):
        outside = self.base/'outside'; outside.write_text('untouched')
        def replace(count,*args):
            if count == 5:
                path = self.root/'new-skill/SKILL.md'; path.unlink(); path.symlink_to(outside)
        self.hooks['mutation'] = replace
        self.assert_unknown(self.request())
        self.assertEqual(outside.read_text(),'untouched')

    def test_short_writes_complete_exact_bytes_but_zero_write_is_unknown(self):
        original = os.write
        with patch('artex.server.skill_create.os.write',side_effect=lambda fd,data:original(fd,data[:7])) as write:
            self.assertEqual(self.request(),(201,{'name':'new-skill'}))
            self.assertGreater(write.call_count,1)
        self.assertEqual((self.root/'new-skill/SKILL.md').read_text(),render_skill('new-skill','Synthetic 世界'))
        with patch('artex.server.skill_create.os.write',return_value=0) as write:
            self.assert_unknown(self.request({'name':'other','description':'d'}))
            write.assert_called_once()
        self.assertEqual((self.root/'other/SKILL.md').read_bytes(),b'')

    def test_file_close_exception_after_real_close_is_unknown_with_written_effect(self):
        original = os.close; failed = []
        def close(fd):
            regular = stat.S_ISREG(os.fstat(fd).st_mode)
            original(fd)
            if regular and not failed:
                failed.append(fd); raise OSError('synthetic close result uncertainty')
        with patch('artex.server.skill_create.os.close',side_effect=close):
            self.assert_unknown(self.request())
        self.assertEqual(len(failed),1)
        self.assertEqual((self.root/'new-skill/SKILL.md').read_text(),render_skill('new-skill','Synthetic 世界'))

    def test_first_fsync_failure_keeps_effect_and_reports_unknown_without_retry(self):
        with patch('artex.server.skill_create.os.fsync',side_effect=OSError('synthetic private detail')) as sync:
            self.assert_unknown(self.request())
            sync.assert_called_once()
        self.assertEqual(parse_skill((self.root/'new-skill/SKILL.md').read_text()).name,'new-skill')

    def test_mkdir_effect_then_exception_is_unknown_not_rolled_back(self):
        original = os.mkdir
        def uncertain(*args,**kwargs):
            original(*args,**kwargs); raise OSError('synthetic post-effect failure')
        with patch('artex.server.skill_create.os.mkdir',side_effect=uncertain) as mkdir:
            self.assert_unknown(self.request()); mkdir.assert_called_once()
        self.assertEqual(list((self.root/'new-skill').iterdir()),[])

    def test_guard_exit_revocation_withholds_success_but_keeps_created_file(self):
        self.guard_exit = lambda:self.grants.__setitem__('mutation',False)
        self.assert_unknown(self.request())
        self.assertTrue((self.root/'new-skill/SKILL.md').exists())

    def test_after_headers_revocation_withholds_receipt(self):
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'):
            self.request(start=lambda:self.grants.__setitem__('current',False))
        self.assertTrue((self.root/'new-skill/SKILL.md').exists())

    def test_composition_is_inert_and_authority_replacement_denies(self):
        with patch('artex.server.skill_create.ScopedFilesystem',side_effect=AssertionError('No IO')):
            bind_skill_creation(self.original,service=self.service,authorize_global=self.global_grant)
        self.service.current = lambda *args:True
        self.assertEqual(self.request()[0],503)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_nonprivate_root_and_unsupported_metadata_deny_before_creation(self):
        self.root.chmod(0o755)
        self.assertEqual(self.request()[0],400)
        self.root.chmod(0o700)
        from artex.tools.filesystem import FilesystemError
        with patch('artex.server.skill_create.ScopedFilesystem._check_metadata',side_effect=FilesystemError('unsupported metadata')):
            self.assertEqual(self.request()[0],400)
        self.assertEqual(list(self.root.iterdir()),[])

    def foreign_service(self):
        root = self.base/'foreign'; root.mkdir(mode=0o700)
        info = root.stat(); scope = SkillDirectoryScope(str(root),info.st_dev,info.st_ino,'foreign')
        service = SkillCreateService(scope,authorize=lambda *a:True,mutation_authorize=lambda *a:True,
                                     current=lambda *a:True,writer_guard=self.guard)
        return root,service

    def test_same_function_foreign_service_owner_is_rejected_before_binding(self):
        foreign,other = self.foreign_service()
        for name in ('handle','validate','_root_current','_observe'):
            original = getattr(self.service,name)
            setattr(self.service,name,getattr(other,name))
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    bind_skill_creation(self.original,service=self.service,authorize_global=self.global_grant)
            finally: setattr(self.service,name,original)
        self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(list(foreign.iterdir()),[])

    def test_same_function_foreign_service_owner_is_rejected_after_binding(self):
        foreign,other = self.foreign_service()
        for name in ('handle','validate','_root_current','_observe'):
            original = getattr(self.service,name)
            setattr(self.service,name,getattr(other,name))
            try:
                with self.subTest(name=name): self.assertEqual(self.request()[0],503)
            finally: setattr(self.service,name,original)
        self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(list(foreign.iterdir()),[])

    def test_foreign_base_method_owners_are_rejected_before_and_after_binding(self):
        foreign_auth = AuthService(Mock(),Mock(),b'another-synthetic-create-key-only')
        foreign_app = WSGIApplication(foreign_auth.router())
        cases = ((self.original,'__call__',foreign_app.__call__),
                 (self.original,'_handle',foreign_app._handle),
                 (self.original.router,'dispatch',foreign_app.router.dispatch),
                 (self.auth,'authorized',foreign_auth.authorized))
        for obj,name,replacement in cases:
            original = getattr(obj,name); setattr(obj,name,replacement)
            try:
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        bind_skill_creation(self.original,service=self.service,authorize_global=self.global_grant)
                    self.assertEqual(self.request()[0],503)
            finally: setattr(obj,name,original)
        self.assertEqual(list(self.root.iterdir()),[])


class ComposerTests(unittest.TestCase):
    def setUp(self):
        import test_skills_workspace_setup as fixture
        self.f = fixture.Tests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.allowed = True
        self.create = SkillCreateService(self.f.scope,authorize=lambda *args:self.allowed,
            mutation_authorize=lambda *args:self.allowed,current=lambda *args:self.f.open,writer_guard=self.f.guard)

    def bind(self,**changes):
        args = dict(create_service=self.create,authorize_create=lambda *args:self.allowed)
        args.update(changes)
        bundle = self.f.bind(**args); self.f.app = bundle.application
        return bundle

    def test_optional_creation_requires_both_explicit_service_and_grant(self):
        with self.assertRaises(ValueError): self.f.bind(create_service=self.create)
        with self.assertRaises(ValueError): self.f.bind(authorize_create=lambda *args:True)
        self.f.login()
        self.assertEqual(self.f.request('/api/skills','POST',{'name':'x','description':'d'})[0],405)

    def test_single_app_create_catalog_read_edit_reload_and_duplicate(self):
        bundle = self.bind(); self.assertIs(bundle.create,self.create); self.f.login()
        self.assertEqual(self.f.request('/api/skills','POST',{'name':'測試','description':'Synthetic 世界'})[:2],
                         (201,{'name':'測試'}))
        names = [item['name'] for item in self.f.request('/api/skills')[1]['skills']]
        self.assertIn('測試',names)
        path = '/api/skills/測試/files/SKILL.md'
        content = self.f.request(path)[1]['content']
        self.assertEqual(parse_skill(content).description,'Synthetic 世界')
        self.assertEqual(self.f.request(path,'PUT',{'content':content+'\nEdited inert text.'})[0],200)
        self.assertTrue(self.f.request(path)[1]['content'].endswith('Edited inert text.'))
        self.assertEqual(self.f.request('/api/skills','POST',{'name':'測試','description':'replace'})[0],409)
        self.assertTrue(self.f.request(path)[1]['content'].endswith('Edited inert text.'))

    def test_creation_grant_does_not_inherit_read_grants(self):
        self.bind(); self.f.login(); self.allowed = False
        self.assertEqual(self.f.request('/api/skills')[0],200)
        self.assertEqual(self.f.request('/api/skills','POST',{'name':'new','description':'d'})[0],403)
        self.assertFalse((self.f.root/'new').exists())

    def test_same_root_and_exact_shared_guard_required(self):
        scope = SkillDirectoryScope(self.f.scope.root,self.f.scope.device,self.f.scope.inode,'foreign')
        for altered in (SkillCreateService(scope,authorize=lambda *a:True,mutation_authorize=lambda *a:True,
                                           current=lambda *a:True,writer_guard=self.f.guard),
                        SkillCreateService(self.f.scope,authorize=lambda *a:True,mutation_authorize=lambda *a:True,
                                           current=lambda *a:True,writer_guard=lambda *a:self.f.guard())):
            with self.assertRaises(PermissionError): self.bind(create_service=altered)


if __name__ == '__main__': unittest.main()
