"""Only temporary synthetic roots. No execution, providers or database."""
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

from artex.skills import parse_skill
from artex.skills.file_listing import SkillDirectoryScope
from artex.server.auth import AuthService, sign_jwt
from artex.server.wsgi import WSGIApplication
from artex.server.skill_archive_import import SkillArchiveImportService,bind_skill_archive_imports,MAX_JSON_BYTES
from test_skill_archive_plan import archive,MD


def multipart(data,filename='fixture.zip'):
    return (b'--synthetic\r\nContent-Disposition: form-data; name="file"; filename="'+filename.encode()+
            b'"\r\nContent-Type: application/zip\r\n\r\n'+data+b'\r\n--synthetic--\r\n')


class ImportTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.base=Path(temp.name);self.root=self.base/'root';self.root.mkdir(mode=0o700)
        info=self.root.stat();self.scope=SkillDirectoryScope(str(self.root),info.st_dev,info.st_ino,'synthetic')
        self.grants=dict(current=True,source=True,mutation=True,import_=True,global_=True)
        self.hooks={};self.counts=dict.fromkeys(self.grants,0);self.proposals=[];self.guard_exit=None
        self.lock=threading.RLock()
        def grant(key):
            def call(*args):
                self.counts[key]+=1
                if key=='import_':self.proposals.append(args[1])
                if key in self.hooks:self.hooks[key](self.counts[key],*args)
                return self.grants[key]
            return call
        @contextmanager
        def guard(*args):
            with self.lock:
                yield
                if self.guard_exit:self.guard_exit()
        self.guard=guard
        self.service=SkillArchiveImportService(self.scope,authorize=grant('source'),mutation_authorize=grant('mutation'),
            import_authorize=grant('import_'),current=grant('current'),writer_guard=guard)
        self.auth=AuthService(Mock(),Mock(),b'synthetic-archive-import-key-only!!')
        self.original=WSGIApplication(self.auth.router(),max_body_bytes=MAX_JSON_BYTES)
        self.global_grant=grant('global_')
        self.app=bind_skill_archive_imports(self.original,service=self.service,authorize_global=self.global_grant)
        self.token=sign_jwt(self.auth.key)

    def response(self,*,data=None,body=None,filename='fixture.zip',method='POST',target='/api/skills/upload',auth=True,start=None):
        body=multipart(archive() if data is None else data,filename) if body is None else body
        path,sep,query=target.partition('?')
        env=dict(REQUEST_METHOD=method,PATH_INFO=path,QUERY_STRING=query,CONTENT_LENGTH=str(len(body)),
                 CONTENT_TYPE='multipart/form-data; boundary=synthetic',**{'wsgi.input':io.BytesIO(body)})
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+self.token
        result=[]
        def begin(status,headers):
            result.append((int(status.split()[0]),dict(headers)))
            if start:start()
        iterator=self.app(env,begin)
        return result,iterator

    def request(self,**kwargs):
        result,iterator=self.response(**kwargs)
        try:raw=b''.join(iterator)
        finally:
            if callable(getattr(iterator,'close',None)):iterator.close()
        return result[0][0],json.loads(raw) if raw else None

    def unknown(self,result):
        self.assertEqual(result[0],500);self.assertEqual(result[1]['outcome'],'unknown');self.assertIs(result[1]['retry'],False)

    def test_real_original_jwt_and_exact_inert_nested_receipt(self):
        data=archive([('wrapper/SKILL.md',MD),('wrapper/assets/data.bin',b'\0\xff'),('wrapper/empty/',b'')])
        self.assertEqual(self.request(data=data),(201,{'name':'imported-skill','files':2}))
        root=self.root/'imported-skill'
        self.assertEqual((root/'assets/data.bin').read_bytes(),b'\0\xff')
        self.assertEqual(parse_skill((root/'SKILL.md').read_text()).name,'imported-skill')
        for p in (root,root/'assets',root/'empty'):self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o700)
        for p in (root/'SKILL.md',root/'assets/data.bin'):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600);self.assertEqual(p.stat().st_nlink,1)
        proposal=self.proposals[-1];self.assertIs(proposal.scope,self.scope)
        self.assertEqual(proposal.files,2);self.assertEqual(proposal.content_bytes,len(MD)+2)

    def test_unicode_name_and_path(self):
        data=archive([('SKILL.md',MD.replace(b'imported-skill','測試'.encode())),('資料/文.txt','世界'.encode())])
        self.assertEqual(self.request(data=data),(201,{'name':'測試','files':2}))
        self.assertEqual((self.root/'測試/資料/文.txt').read_text(),'世界')

    def test_auth_and_every_independent_grant_before_filesystem(self):
        self.assertEqual(self.request(auth=False)[0],401);self.assertEqual(self.counts,dict.fromkeys(self.grants,0))
        for key in self.grants:
            with self.subTest(key=key):
                self.grants[key]=False
                with patch('artex.server.skill_archive_import.ScopedFilesystem',side_effect=AssertionError()):
                    self.assertEqual(self.request()[0],403)
                self.grants[key]=True
        self.assertEqual(list(self.root.iterdir()),[])

    def test_inert_construction_base_health_and_unsupported_methods(self):
        self.assertEqual(list(self.root.iterdir()),[])
        for method in ('GET','HEAD','PUT','DELETE'):
            self.assertEqual(self.request(method=method)[0],405)
        self.assertEqual(self.request(method='post')[0],400)
        self.assertEqual(self.request(method='GET',target='/api/health',body=b'',auth=False)[0],200)

    def test_invalid_multipart_query_and_filename_have_no_effect(self):
        valid=multipart(archive())
        for kwargs in ({'target':'/api/skills/upload?overwrite=true'},{'filename':'x.txt'},
                       {'filename':'../x.zip'},{'filename':'a\\x.zip'},{'filename':'a"x.zip'},
                       {'body':valid+b'x'},{'body':valid.replace(b'name="file"',b'name="other"')},
                       {'body':valid.replace(b'Content-Type: application/zip',b'Content-Transfer-Encoding: base64')},
                       {'body':b'not multipart'}):
            with self.subTest(kwargs=str(kwargs)[:60]):self.assertEqual(self.request(**kwargs)[0],400)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_complete_archive_validation_precedes_any_mkdir(self):
        invalid=archive([('SKILL.md',MD),('late.txt',b'late-content')],method=0).replace(b'late-content',b'Late-content')
        with patch('artex.server.skill_archive_import.os.mkdir',side_effect=AssertionError()):
            self.assertEqual(self.request(data=invalid)[0],400)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_existing_any_kind_conflicts_no_overwrite(self):
        destination=self.root/'imported-skill'
        for kind in ('file','directory','symlink'):
            if kind=='file':destination.write_bytes(b'original')
            elif kind=='directory':destination.mkdir(mode=0o700)
            else:destination.symlink_to(self.base/'absent')
            with patch('artex.server.skill_archive_import.os.mkdir',side_effect=AssertionError()):
                self.assertEqual(self.request()[0],409)
            if kind=='directory':destination.rmdir()
            else:destination.unlink()

    def test_original_root_replacement_at_last_pre_effect_grant_denied(self):
        def hook(count,*args):
            if count==3:
                self.root.rename(self.base/'old');self.root.mkdir(mode=0o700)
        self.hooks['global_']=hook
        self.assertEqual(self.request()[0],409)
        self.assertEqual(list(self.root.iterdir()),[]);self.assertEqual(list((self.base/'old').iterdir()),[])

    def test_request_or_immutable_plan_tampering_denied(self):
        mutations=(lambda req,p:object.__setattr__(req,'target','/wrong'),
                   lambda req,p:object.__setattr__(p,'files',True),
                   lambda req,p:object.__setattr__(p.selection.plan.files[0],'path','elsewhere'),
                   lambda req,p:object.__setattr__(p.selection,'name','other'))
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.hooks['mutation']=lambda n,req,p:mutate(req,p)
                self.assertEqual(self.request()[0],403);self.assertEqual(list(self.root.iterdir()),[])

    def test_named_parent_displacement_after_real_creation_stops_before_file_write(self):
        def hook(count,*args):
            if count==4:(self.root/'imported-skill').rename(self.root/'moved')
        self.hooks['import_']=hook
        with patch('artex.server.skill_archive_import.os.write',wraps=os.write) as write:
            self.unknown(self.request());self.assertEqual(write.call_count,0)
        self.assertEqual(list((self.root/'moved').iterdir()),[])

    def test_late_parent_symlink_not_followed(self):
        outside=self.base/'outside';outside.mkdir()
        def hook(count,*args):
            if count==4:
                (self.root/'imported-skill').rename(self.root/'moved')
                (self.root/'imported-skill').symlink_to(outside,target_is_directory=True)
        self.hooks['global_']=hook
        self.unknown(self.request());self.assertEqual(list(outside.iterdir()),[])

    def test_late_hardlink_is_not_written(self):
        def hook(count,*args):
            file=self.root/'imported-skill/SKILL.md'
            if file.exists() and not (self.base/'linked').exists():os.link(file,self.base/'linked')
        self.hooks['import_']=hook
        with patch('artex.server.skill_archive_import.os.write',wraps=os.write) as write:
            self.unknown(self.request());self.assertEqual(write.call_count,0)
        self.assertEqual((self.base/'linked').read_bytes(),b'')

    def test_any_grant_revocation_after_mkdir_is_unknown_and_no_rollback(self):
        for key in self.grants:
            with self.subTest(key=key):
                self.setUp()
                self.hooks[key]=lambda count,*args,k=key:self.grants.update({k:False}) if count==4 else None
                self.unknown(self.request());self.assertTrue((self.root/'imported-skill').is_dir())

    def test_actual_post_effect_fsync_failure_preserves_partial_state(self):
        with patch('artex.server.skill_archive_import.os.fsync',side_effect=OSError('private detail')):
            result=self.request();self.unknown(result);self.assertNotIn('private detail',str(result))
        self.assertEqual((self.root/'imported-skill/SKILL.md').read_bytes(),MD)
        self.assertEqual(self.request()[0],409)

    def test_attempted_mkdir_error_and_open_error_do_not_retry(self):
        with patch('artex.server.skill_archive_import.os.mkdir',side_effect=PermissionError()) as mkdir:
            self.unknown(self.request());self.assertEqual(mkdir.call_count,1)
        real=os.open
        def opened(path,flags,*args,**kwargs):
            if flags & os.O_CREAT:raise OSError()
            return real(path,flags,*args,**kwargs)
        with patch('artex.server.skill_archive_import.os.open',side_effect=opened):self.unknown(self.request())
        self.assertEqual(list((self.root/'imported-skill').iterdir()),[])

    def test_partial_write_short_count_and_interrupt_are_unknown(self):
        real=os.write
        def write(fd,data):real(fd,data[:3]);raise KeyboardInterrupt()
        with patch('artex.server.skill_archive_import.os.write',side_effect=write) as fn:
            self.unknown(self.request());self.assertEqual(fn.call_count,1)
        self.assertEqual((self.root/'imported-skill/SKILL.md').read_bytes(),MD[:3])

    def test_successful_short_writes_are_supported_and_zero_write_unknown(self):
        real=os.write
        with patch('artex.server.skill_archive_import.os.write',side_effect=lambda fd,data:real(fd,data[:7])):
            self.assertEqual(self.request()[0],201)
        self.setUp()
        with patch('artex.server.skill_archive_import.os.write',return_value=0):self.unknown(self.request())

    def test_post_write_close_failure_is_unknown_without_cleanup(self):
        realclose=os.close;realwrite=os.write;written=set()
        def write(fd,data):written.add(fd);return realwrite(fd,data)
        def close(fd):
            realclose(fd)
            if fd in written:written.remove(fd);raise OSError()
        with patch('artex.server.skill_archive_import.os.write',side_effect=write),patch('artex.server.skill_archive_import.os.close',side_effect=close):
            self.unknown(self.request())
        self.assertEqual((self.root/'imported-skill/SKILL.md').read_bytes(),MD)

    def test_guard_exit_revocation_unknown(self):
        self.guard_exit=lambda:self.grants.update(import_=False)
        self.unknown(self.request());self.assertEqual((self.root/'imported-skill/SKILL.md').read_bytes(),MD)

    def test_after_headers_revocation_withholds_receipt_not_effect(self):
        result,body=self.response(start=lambda:self.grants.update(import_=False))
        self.assertEqual(result[0][0],201)
        with self.assertRaises(RuntimeError):next(body)
        body.close();self.assertEqual((self.root/'imported-skill/SKILL.md').read_bytes(),MD)

    def test_after_headers_replacement_withheld_without_removing_replacement(self):
        def replace():(self.root/'imported-skill/SKILL.md').write_bytes(b'changed')
        result,body=self.response(start=replace)
        self.assertEqual(result[0][0],201)
        with self.assertRaises(RuntimeError):next(body)
        body.close();self.assertEqual((self.root/'imported-skill/SKILL.md').read_bytes(),b'changed')

    def test_never_consumed_and_repeated_response_does_not_repeat_mutation(self):
        with patch('artex.server.skill_archive_import.os.mkdir',wraps=os.mkdir) as mkdir:
            result,body=self.response();body.close();self.assertEqual(list(body),[]);self.assertEqual(mkdir.call_count,1)
        self.assertEqual(result[0][0],201)

    def test_foreign_bound_service_methods_rejected_before_and_after_bind(self):
        foreign=SkillArchiveImportService(self.scope,authorize=lambda *a:True,mutation_authorize=lambda *a:True,
            import_authorize=lambda *a:True,current=lambda *a:True,writer_guard=self.guard)
        for name in ('handle','validate','_root_current','_observe'):
            with self.subTest(name=name):
                original=getattr(self.service,name);setattr(self.service,name,getattr(foreign,name))
                with self.assertRaises(ValueError):bind_skill_archive_imports(self.original,service=self.service,authorize_global=self.global_grant)
                self.assertEqual(self.request()[0],503);setattr(self.service,name,original)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_policy_base_method_route_drift_and_wrapper_stacking_refused(self):
        with self.assertRaises(ValueError):bind_skill_archive_imports(self.app,service=self.service,authorize_global=self.global_grant)
        self.service.import_authorize=lambda *a:True
        self.assertEqual(self.request()[0],503)
        self.service.import_authorize=self.service._pins[3]
        self.original.router.add('GET','/new',lambda *a:None)
        self.assertEqual(self.request()[0],503);self.assertEqual(list(self.root.iterdir()),[])

    def test_foreign_original_base_method_owners_refused(self):
        other_auth=AuthService(Mock(),Mock(),b'other-synthetic-import-key-only!!')
        other=WSGIApplication(other_auth.router(),max_body_bytes=MAX_JSON_BYTES)
        for owner,foreign,name in ((self.original,other,'__call__'),(self.original,other,'_handle'),
                                   (self.original.router,other.router,'dispatch'),(self.auth,other_auth,'authorized')):
            with self.subTest(name=name):
                original=getattr(owner,name);setattr(owner,name,getattr(foreign,name))
                with self.assertRaises(ValueError):bind_skill_archive_imports(self.original,service=self.service,authorize_global=self.global_grant)
                self.assertEqual(self.request()[0],503);setattr(owner,name,original)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_final_disclosure_guard_exit_revocation_withholds_body(self):
        exits=[]
        def exit_guard():
            exits.append(True)
            if len(exits)==3:self.grants['import_']=False
        self.guard_exit=exit_guard
        result,body=self.response();self.assertEqual(result[0][0],201)
        with self.assertRaises(RuntimeError):next(body)
        body.close();self.assertEqual(len(exits),3)

    def test_extra_file_added_during_callback_is_not_overwritten_or_removed(self):
        def hook(count,*args):
            if count==4:(self.root/'imported-skill/extra').write_bytes(b'keep')
        self.hooks['mutation']=hook
        self.unknown(self.request());self.assertEqual((self.root/'imported-skill/extra').read_bytes(),b'keep')
        self.assertFalse((self.root/'imported-skill/SKILL.md').exists())

    def test_second_file_failure_keeps_first_and_partial_new_directory(self):
        data=archive([('SKILL.md',MD),('second.bin',b'second')]);real=os.open
        def opened(path,flags,*args,**kwargs):
            if path=='second.bin' and flags & os.O_CREAT:raise OSError('synthetic')
            return real(path,flags,*args,**kwargs)
        with patch('artex.server.skill_archive_import.os.open',side_effect=opened):self.unknown(self.request(data=data))
        self.assertEqual((self.root/'imported-skill/SKILL.md').read_bytes(),MD)
        self.assertFalse((self.root/'imported-skill/second.bin').exists())

    def test_directories_count_in_limits_not_receipt_and_empty_file_is_written(self):
        self.assertEqual(self.request(data=archive([('SKILL.md',MD),('empty/',b''),('zero',b'')])),
                         (201,{'name':'imported-skill','files':2}))
        self.assertEqual((self.root/'imported-skill/zero').read_bytes(),b'')

    def test_concurrent_duplicates_have_one_success_and_no_replacement(self):
        results=[];errors=[]
        def invoke():
            try:results.append(self.request()[0])
            except BaseException as exc:errors.append(exc)
        threads=[threading.Thread(target=invoke) for _ in range(4)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(sorted(results),[201,409,409,409])
        self.assertEqual((self.root/'imported-skill/SKILL.md').read_bytes(),MD)
