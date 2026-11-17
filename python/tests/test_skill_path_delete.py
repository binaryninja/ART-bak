"""Permanent removal tests use only temporary self-owned synthetic data."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from artex.server.auth import AuthService, sign_jwt
from artex.server.core import Request, json_response
from artex.server.wsgi import WSGIApplication
from artex.server.skill_path_delete import SkillPathDeleteService, bind_skill_path_deletes
from artex.skills.file_listing import SkillDirectoryScope
from artex.tools.filesystem import ScopedFilesystem
from artex.tools.workspace_mutations import WorkspaceMutations


class DeleteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name); self.root = self.base/'root'; self.root.mkdir(mode=0o700)
        self.skill = self.root/'sample'; self.skill.mkdir(); (self.skill/'existing').mkdir()
        self.file = self.skill/'existing'/'file.txt'; self.file.write_bytes(b'synthetic data')
        (self.skill/'SKILL.md').write_text('inert manifest')
        info = self.root.stat(); self.scope = SkillDirectoryScope(str(self.root),info.st_dev,info.st_ino,'fixture')
        self.grants = dict(source=True, mutation=True, delete=True, current=True, global_=True)
        self.counts = dict.fromkeys(self.grants,0); self.hooks = {}; self.proposals = []
        self.guard_exit = None; self.guard_depth = 0; self.guard_exits = 0
        self.lock = threading.RLock()
        def grant(key):
            def check(*args):
                self.counts[key] += 1
                if key == 'delete': self.proposals.append(args[1])
                if key in self.hooks: self.hooks[key](self.counts[key],*args)
                return self.grants[key]
            return check
        @contextmanager
        def guard(*args):
            with self.lock:
                self.guard_depth += 1
                try: yield
                finally:
                    self.guard_depth -= 1; self.guard_exits += 1
                    if self.guard_exit: self.guard_exit(self.guard_exits)
        self.options = dict(authorize=grant('source'),mutation_authorize=grant('mutation'),
                            delete_authorize=grant('delete'),current=grant('current'),writer_guard=guard)
        self.global_grant = grant('global_')
        self.service = SkillPathDeleteService(self.scope,**self.options)
        self.auth = AuthService(object(),object(),b'synthetic-skill-delete-key-32bytes')
        self.original = WSGIApplication(self.auth.router()); self.token = sign_jwt(self.auth.key)
        self.bind()

    def bind(self):
        self.app = bind_skill_path_deletes(self.original,service=self.service,authorize_global=self.global_grant)

    def response(self, path='existing/file.txt', *, target=None, method='DELETE', body=b'', auth=True, begin=None):
        if target is None: target = '/api/skills/sample/files/'+path
        name, _, query = target.partition('?')
        env = dict(REQUEST_METHOD=method,PATH_INFO=name.encode('utf-8').decode('latin-1'),
                   QUERY_STRING=query,CONTENT_LENGTH=str(len(body)),**{'wsgi.input':io.BytesIO(body)})
        if auth: env['HTTP_AUTHORIZATION'] = 'Bearer '+self.token
        received = []
        def start(status,headers):
            received.append((int(status.split()[0]),dict(headers)))
            if begin: begin()
        iterator = self.app(env,start)
        return received,iterator

    def request(self, *args, **kwargs):
        received, iterator = self.response(*args,**kwargs)
        try: raw = b''.join(iterator)
        finally:
            if callable(getattr(iterator,'close',None)): iterator.close()
        status, headers = received[0]
        if kwargs.get('method') != 'HEAD': self.assertEqual(headers['Content-Length'],str(len(raw)))
        return status,json.loads(raw) if raw else None,headers

    def assert_unknown(self, result):
        self.assertEqual(result[0],500)
        self.assertEqual(result[1]['outcome'],'unknown'); self.assertIs(result[1]['retry'],False)

    def test_construction_inert_original_routes_unchanged(self):
        with patch.object(ScopedFilesystem,'__init__',side_effect=AssertionError('no I/O')):
            self.bind()
            self.assertEqual(self.request(target='/api/health',method='GET')[0],200)
        self.assertEqual(self.counts,dict.fromkeys(self.grants,0))
        self.assertFalse(any('/skills/' in path for method,path,*other in self.original.router._routes))
        self.assertTrue(self.file.exists())

    def test_auth_and_unsupported_methods_do_not_touch_filesystem(self):
        with patch.object(ScopedFilesystem,'__init__',side_effect=AssertionError('no I/O')):
            self.assertEqual(self.request(auth=False)[0],401)
            for method in ('GET','HEAD','POST','PUT','delete'):
                response = self.request(method=method)
                self.assertEqual((response[0],response[2]['Allow']),(405,'DELETE'))
        self.assertEqual(self.counts,dict.fromkeys(self.grants,0))

    def test_exact_receipt_single_unlink_guard_and_immutable_observed_proposal(self):
        before = self.file.stat(); original = os.unlink
        def unlink(*args,**kwargs):
            self.assertGreater(self.guard_depth,0); return original(*args,**kwargs)
        with patch('artex.tools.workspace_mutations.os.unlink',side_effect=unlink) as removal:
            self.assertEqual(self.request()[:2],(200,{'deleted':'existing/file.txt'}))
        removal.assert_called_once(); self.assertFalse(self.file.exists())
        self.assertEqual((self.skill/'SKILL.md').read_text(),'inert manifest')
        proposal = self.proposals[0]
        self.assertIs(proposal.scope,self.scope)
        self.assertEqual((proposal.selection.name,proposal.selection.path),('sample','existing/file.txt'))
        self.assertEqual(proposal.target.path,'sample/existing/file.txt')
        self.assertEqual(proposal.target.action,'delete')
        self.assertEqual(proposal.target.target_fingerprint,ScopedFilesystem._stat(before))
        self.assertEqual(proposal.target.parent_identity,(self.file.parent.stat().st_dev,self.file.parent.stat().st_ino))
        self.assertTrue(all(p is proposal for p in self.proposals))

    def test_empty_directory_removed_without_recursion_and_missing_is_404(self):
        (self.skill/'empty').mkdir()
        self.assertEqual(self.request('empty')[:2],(200,{'deleted':'empty'}))
        self.assertFalse((self.skill/'empty').exists())
        self.assertEqual(self.request('empty')[0],404)
        self.assertEqual(self.request('absent/file')[0],404)
        self.assertEqual(self.request(target='/api/skills/absent/files/file')[0],404)
        self.assertTrue(self.file.exists())

    def test_nonempty_directory_refuses_without_traversal_or_child_removal(self):
        with patch('os.unlink',side_effect=AssertionError('no recursive unlink')):
            self.assertEqual(self.request('existing')[0],409)
        self.assertEqual(self.file.read_bytes(),b'synthetic data')

    def test_top_level_manifest_aliases_forbidden_but_nested_manifest_is_data(self):
        with patch.object(ScopedFilesystem,'__init__',side_effect=AssertionError('no I/O')):
            for name in ('SKILL.md','skill.md','Skill.MD','ſkill.md'):
                self.assertEqual(self.request(name)[0],403)
        nested = self.skill/'existing/SKILL.md'; nested.write_text('synthetic nested data')
        self.assertEqual(self.request('existing/SKILL.md')[0],200)
        self.assertTrue((self.skill/'SKILL.md').exists())

    def test_request_and_path_bounds_reject_before_effect(self):
        paths = ('../SKILL.md','existing/../SKILL.md','/file','a//b','a/./b','a\\b','a%2fb','a#b',
                 'a'*256,'/'.join(['a']*33),'x\x00','a\n','a\ufffd')
        for path in paths:
            self.assertEqual(self.request(path)[0],400,path)
        self.assertEqual(self.request(body=b'{}')[0],400)
        self.assertEqual(self.request(target='/api/skills/sample/files/existing/file.txt?x=1')[0],400)
        self.assertTrue(self.file.exists())

    def test_unicode_carrier_is_decoded_once_and_percent_aliases_are_not(self):
        directory = self.root/'測試'; directory.mkdir(); target = directory/'資料.txt'; target.write_text('synthetic')
        self.assertEqual(self.request(target='/api/skills/測試/files/資料.txt')[:2],(200,{'deleted':'資料.txt'}))
        self.assertFalse(target.exists())
        for target in ('/api/skills/%E6%B8%AC%E8%A9%A6/files/file','/api/skills/sample/files/%2e%2e/SKILL.md'):
            self.assertEqual(self.request(target=target)[0],400)

    def test_source_current_global_denial_prevents_any_filesystem_access(self):
        for key in ('source','current','global_'):
            self.grants[key] = False
            with patch.object(ScopedFilesystem,'__init__',side_effect=AssertionError('no I/O')):
                self.assertEqual(self.request()[0],403,key)
            self.grants[key] = True
        self.assertTrue(self.file.exists())

    def test_mutation_and_permanent_delete_grants_are_independent_before_remove(self):
        for key in ('mutation','delete'):
            self.grants[key] = False
            with patch('os.unlink',side_effect=AssertionError('no removal')):
                self.assertEqual(self.request()[0],403,key)
            self.grants[key] = True
        self.assertTrue(self.file.exists())

    def test_symlink_hardlink_special_and_symlink_parent_refused(self):
        outside = self.base/'outside'; outside.write_text('untouched')
        (self.skill/'link').symlink_to(outside)
        (self.skill/'dangling').symlink_to(self.base/'absent')
        os.link(outside,self.skill/'hard')
        os.mkfifo(self.skill/'pipe')
        (self.skill/'dirlink').symlink_to(self.file.parent,target_is_directory=True)
        for path in ('link','dangling','hard','pipe','dirlink/file.txt'):
            self.assertEqual(self.request(path)[0],400,path)
        self.assertEqual(outside.read_text(),'untouched'); self.assertTrue(self.file.exists())

    def test_named_parent_replacement_in_second_grant_is_detected_before_unlink(self):
        def replace(count,*args):
            if count == 2:
                self.file.parent.rename(self.skill/'renamed'); (self.skill/'existing').mkdir()
        self.hooks['mutation'] = replace
        with patch('os.unlink',side_effect=AssertionError('no removal')):
            self.assertEqual(self.request()[0],409)
        self.assertEqual((self.skill/'renamed/file.txt').read_bytes(),b'synthetic data')
        self.assertFalse(self.file.exists())

    def test_target_change_in_last_primitive_grant_is_not_removed(self):
        def change(count,*args):
            if count == 2: self.file.write_bytes(b'new synthetic version')
        self.hooks['delete'] = change
        with patch('os.unlink',side_effect=AssertionError('no removal')):
            self.assertEqual(self.request()[0],409)
        self.assertEqual(self.file.read_bytes(),b'new synthetic version')

    def test_late_link_target_replacements_are_not_removed(self):
        outside = self.base/'outside'; outside.write_text('untouched')
        for kind in ('symlink','hardlink'):
            with self.subTest(kind=kind):
                self.setUp()
                outside = self.base/'outside'; outside.write_text('untouched')
                def replace(count,*args):
                    if count == 2:
                        self.file.rename(self.file.with_name('original'))
                        if kind == 'symlink': self.file.symlink_to(outside)
                        else: os.link(outside,self.file)
                self.hooks['delete'] = replace
                with patch('os.unlink',side_effect=AssertionError('no removal')):
                    self.assertEqual(self.request()[0],400)
                self.assertEqual(outside.read_text(),'untouched')
                self.assertTrue(self.file.with_name('original').exists())

    def test_root_replacement_before_primitive_is_detected(self):
        def replace(count,*args):
            if count == 1:
                self.root.rename(self.base/'old-root'); self.root.mkdir(); (self.root/'sample').mkdir()
        self.hooks['mutation'] = replace
        with patch('os.unlink',side_effect=AssertionError('no removal')):
            self.assertEqual(self.request()[0],409)
        self.assertTrue((self.base/'old-root/sample/existing/file.txt').exists())

    def test_proposal_path_scope_selection_and_fingerprint_tampering_denied(self):
        cases = ('path','scope','selection','scalar')
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                def tamper(count,request,proposal):
                    if count != 1: return
                    if case == 'path': object.__setattr__(proposal.target,'path','sample/SKILL.md')
                    elif case == 'scope': object.__setattr__(proposal,'scope',object())
                    elif case == 'selection': object.__setattr__(proposal.selection,'path','SKILL.md')
                    else:
                        value = list(proposal.target.target_fingerprint); value[5] = True
                        object.__setattr__(proposal.target,'target_fingerprint',tuple(value))
                self.hooks['delete'] = tamper
                self.assertEqual(self.request()[0],403)
                self.assertTrue(self.file.exists()); self.assertTrue((self.skill/'SKILL.md').exists())

    def test_request_mutation_in_grant_cannot_redirect_operation(self):
        def tamper(count,request,proposal):
            if count == 1: object.__setattr__(request,'target','/api/skills/sample/files/SKILL.md')
        self.hooks['delete'] = tamper
        self.assertEqual(self.request()[0],403); self.assertTrue(self.file.exists())

    def test_post_removal_revocation_is_unknown_and_does_not_retry(self):
        def revoke(count,*args):
            if not self.file.exists(): self.grants['delete'] = False
        self.hooks['delete'] = revoke
        original = os.unlink
        with patch('os.unlink',side_effect=original) as removal:
            self.assert_unknown(self.request())
        removal.assert_called_once(); self.assertFalse(self.file.exists())

    def test_fsync_failure_after_removal_is_unknown(self):
        with patch('os.fsync',side_effect=OSError('private sync detail')):
            result = self.request()
        self.assert_unknown(result); self.assertNotIn('private sync',json.dumps(result[1]))
        self.assertFalse(self.file.exists())

    def test_attempt_failure_and_interruption_after_actual_removal_are_unknown(self):
        original = os.unlink
        with patch('os.unlink',side_effect=PermissionError('synthetic attempt')) as removal:
            self.assert_unknown(self.request())
        removal.assert_called_once(); self.assertTrue(self.file.exists())
        def interrupted(*args,**kwargs):
            original(*args,**kwargs); raise KeyboardInterrupt()
        with patch('os.unlink',side_effect=interrupted) as removal:
            self.assert_unknown(self.request())
        removal.assert_called_once(); self.assertFalse(self.file.exists())

    def test_close_failure_after_real_removal_is_unknown(self):
        original = ScopedFilesystem.close; failed = False
        def close(fs):
            nonlocal failed
            original(fs)
            if not self.file.exists() and not failed:
                failed = True; raise OSError('private close detail')
        with patch.object(ScopedFilesystem,'close',close): self.assert_unknown(self.request())
        self.assertTrue(failed); self.assertFalse(self.file.exists())

    def test_reentrant_recreation_after_effect_is_preserved_and_unknown(self):
        def recreate(count,*args):
            if not self.file.exists(): self.file.write_bytes(b'replacement data')
        self.hooks['mutation'] = recreate
        original = os.unlink
        with patch('os.unlink',side_effect=original) as removal: self.assert_unknown(self.request())
        removal.assert_called_once(); self.assertEqual(self.file.read_bytes(),b'replacement data')

    def test_guard_exit_revocation_returns_unknown(self):
        self.guard_exit = lambda count:self.grants.update(delete=False)
        self.assert_unknown(self.request()); self.assertFalse(self.file.exists())

    def test_final_guard_exit_and_post_header_revocation_withhold_receipt(self):
        self.guard_exit = lambda count:self.grants.update(delete=False) if count == 3 else None
        with self.assertRaises(RuntimeError): self.request()
        self.assertFalse(self.file.exists())
        self.setUp()
        with self.assertRaises(RuntimeError): self.request(begin=lambda:self.grants.update(delete=False))
        self.assertFalse(self.file.exists())

    def test_post_header_replacement_is_not_deleted_by_receipt(self):
        original = os.unlink
        with patch('os.unlink',side_effect=original) as removal:
            with self.assertRaises(RuntimeError): self.request(begin=lambda:self.file.write_bytes(b'replacement'))
        removal.assert_called_once(); self.assertEqual(self.file.read_bytes(),b'replacement')

    def test_never_consumed_receipt_close_and_repeated_next_do_not_repeat_delete(self):
        received, body = self.response()
        self.assertEqual(received[0][0],200); self.assertFalse(self.file.exists())
        body.close(); self.assertEqual(list(body),[])
        self.setUp()
        received, body = self.response()
        self.assertEqual(json.loads(next(body)),{'deleted':'existing/file.txt'})
        self.assertEqual(list(body),[]); body.close(); body.close()

    def foreign(self):
        root = self.base/'foreign'; root.mkdir(exist_ok=True)
        info = root.stat(); scope = SkillDirectoryScope(str(root),info.st_dev,info.st_ino,'foreign')
        return SkillPathDeleteService(scope,**self.options)

    def test_foreign_bound_service_owners_rejected_before_and_after_binding(self):
        other = self.foreign()
        for name in ('handle','validate','_root_current','_parent_current','_observe_missing'):
            with self.subTest(method=name):
                original = getattr(self.service,name)
                setattr(self.service,name,getattr(other,name))
                with self.assertRaises(ValueError): self.bind()
                self.assertEqual(self.request()[0],503)
                setattr(self.service,name,original)
                self.assertTrue(self.file.exists())

    def test_foreign_bound_transport_auth_and_router_owners_rejected(self):
        auth = AuthService(object(),object(),b'y'*32); original = WSGIApplication(auth.router())
        pairs = ((self.original,original,'__call__'),(self.original,original,'_handle'),
                 (self.original.router,original.router,'dispatch'),(self.auth,auth,'authorized'))
        for local,foreign,name in pairs:
            with self.subTest(method=name):
                saved = getattr(local,name); setattr(local,name,getattr(foreign,name))
                with self.assertRaises(ValueError): self.bind()
                self.assertEqual(self.request()[0],503)
                setattr(local,name,saved)
                self.assertTrue(self.file.exists())

    def test_policy_and_original_route_drift_refused_and_conflicts_not_stacked(self):
        self.service.delete = lambda *args:True
        self.assertEqual(self.request()[0],503); self.assertTrue(self.file.exists())
        self.service.delete = self.options['delete_authorize']
        self.original.router.add('GET','/api/skills/{other}/files/{path}',lambda r,p:json_response(200,{}))
        self.assertEqual(self.request()[0],503)
        with self.assertRaises(ValueError): self.bind()
        with self.assertRaises(ValueError): bind_skill_path_deletes(self.app,service=self.service,authorize_global=self.global_grant)
