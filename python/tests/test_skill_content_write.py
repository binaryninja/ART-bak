"""Actual temporary POSIX filesystem and JWT/WSGI; no PostgreSQL or user files."""
from contextlib import contextmanager
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler
from urllib.parse import quote

from artex.server.auth import AuthService, sign_jwt
from artex.server.core import Request, json_response
from artex.server.wsgi import WSGIApplication
from artex.server.skill_content_write import SkillContentWriteService, bind_skill_content_writes, MAX_BYTES
from artex.skills.file_listing import SkillDirectoryScope
from artex.tools.filesystem import ScopedFilesystem
from test_application import TestCodec


class Tests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name); (self.root/'sample').mkdir(); (self.root/'sample'/'existing').mkdir()
        info=self.root.stat(); self.scope=SkillDirectoryScope(str(self.root),info.st_dev,info.st_ino,'fixture-r1')
        self.source=True; self.global_grant=True; self.mutate=True; self.current=True
        self.proposals=[]; self.requests=[]; self.guard_depth=0; self.lock=threading.RLock()
        self.auth=AuthService(object(),TestCodec(),b'x'*32)
        self.original=WSGIApplication(self.auth.router())
        self.token=sign_jwt(b'x'*32)
        self.make()

    def make(self, **changes):
        def source(scope, selection): self.requests.append(('source',selection)); return self.source
        def mutation(request, proposal):
            self.proposals.append(proposal); return self.mutate
        @contextmanager
        def guard(scope, selection):
            with self.lock:
                self.guard_depth+=1
                try: yield
                finally: self.guard_depth-=1
        options=dict(authorize=source,mutation_authorize=mutation,current=lambda *a:self.current,writer_guard=guard)
        options.update(changes)
        self.service=SkillContentWriteService(self.scope,**options)
        def global_grant(request, scope, selection): self.requests.append(('global',selection)); return self.global_grant
        self.app=bind_skill_content_writes(self.original,service=self.service,authorize_global=global_grant)

    def request(self, content='new text', *, file='new.txt', target=None, auth=True, method='PUT', raw=None, callback=None):
        if target is None: target='/api/skills/sample/files/'+file
        body=json.dumps({'content':content}).encode() if raw is None else raw
        pathname, _, query=target.partition('?')
        env=dict(REQUEST_METHOD=method,PATH_INFO=pathname.encode('utf-8').decode('latin-1'),QUERY_STRING=query,CONTENT_LENGTH=str(len(body)))
        env['wsgi.input']=io.BytesIO(body)
        if auth: env['HTTP_AUTHORIZATION']='Bearer '+self.token
        result=[]
        def start(status,headers):
            result.append((int(status.split()[0]),dict(headers)))
            if callback: callback()
        iterator=self.app(env,start)
        try: content=b''.join(iterator)
        finally:
            if hasattr(iterator,'close'): iterator.close()
        return result[0][0], json.loads(content) if content else None, result[0][1]

    def test_inert_construction_and_unchanged_original_routes(self):
        self.assertEqual(self.requests,[])
        self.assertFalse(any('/skills/' in path for method,path,*rest in self.original.router._routes))
        self.assertEqual(self.request(target='/api/health',method='GET',raw=b'')[0],200)
        self.assertEqual(list((self.root/'sample').iterdir()),[self.root/'sample/existing'])

    def test_auth_and_wrong_method_before_any_file_access(self):
        with patch.object(ScopedFilesystem,'__init__',side_effect=AssertionError('No filesystem')):
            self.assertEqual(self.request(auth=False)[0],401)
            for method in ('GET','HEAD','POST','DELETE'):
                status,body,headers=self.request(method=method,raw=b'')
                self.assertEqual(status,405); self.assertEqual(headers['Allow'],'PUT')
        self.assertEqual(self.requests,[])

    def test_create_exact_source_contract_mode_and_digest_grant(self):
        original=os.replace
        def replace(*args,**kwargs):
            self.assertGreater(self.guard_depth,0); return original(*args,**kwargs)
        with patch('artex.tools.filesystem.os.replace',side_effect=replace) as write:
            self.assertEqual(self.request('café 資料',file='existing/file.txt')[:2],(200,{'ok':True}))
        write.assert_called_once()
        path=self.root/'sample/existing/file.txt'
        self.assertEqual(path.read_text(),'café 資料')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        proposal=self.proposals[0]
        self.assertIs(proposal.scope,self.scope)
        self.assertEqual((proposal.selection.name,proposal.selection.path),('sample','existing/file.txt'))
        self.assertIsNone(proposal.previous_version)
        self.assertEqual(proposal.content_bytes,len('café 資料'.encode()))
        self.assertEqual(proposal.content_sha256,hashlib.sha256('café 資料'.encode()).hexdigest())

    def test_overwrite_is_atomic_preserves_rwx_strips_special_bits(self):
        path=self.root/'sample/new.txt'; path.write_text('old'); path.chmod(0o6751)
        before=path.stat(); original=os.replace; seen=[]
        def replace(*args,**kwargs):
            seen.append(path.read_text()); return original(*args,**kwargs)
        with patch('artex.tools.filesystem.os.replace',side_effect=replace):
            self.assertEqual(self.request('changed')[:2],(200,{'ok':True}))
        self.assertEqual(seen,['old']); self.assertEqual(path.read_text(),'changed')
        self.assertNotEqual(path.stat().st_ino,before.st_ino)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o751)
        self.assertEqual(self.proposals[0].previous_version[1],hashlib.sha256(b'old').digest())
        self.assertEqual(list((self.root/'sample').glob('.artex-write-*')),[])

    def test_missing_parent_or_skill_is_not_created(self):
        self.assertEqual(self.request(file='missing/file')[0],404)
        self.assertEqual(self.request(target='/api/skills/absent/files/file')[0],404)
        self.assertFalse((self.root/'sample/missing').exists()); self.assertFalse((self.root/'absent').exists())

    def test_independent_grants_deny_before_temporary_write(self):
        for field in ('source','global_grant','current','mutate'):
            setattr(self,field,False)
            with patch.object(ScopedFilesystem,'_call',side_effect=AssertionError('No primitive')):
                self.assertEqual(self.request()[0],403,field)
            setattr(self,field,True)
        self.assertFalse((self.root/'sample/new.txt').exists())
        self.assertEqual(list(self.root.rglob('.artex-write-*')),[])

    def test_strict_json_text_canonical_paths_and_bounds(self):
        for raw in (b'',b'{}',b'[]',b'{"content":null}',b'{"content":"x","content":"y"}',b'{"content":"x","extra":1}',b'NaN',b'{"content":"\\ud800"}'):
            self.assertEqual(self.request(raw=raw)[0],400,raw)
        for file in ('.','..','../escape','a/../b','/escape','a//b','./x','a\\b','a%2fb','a\x00b','a/','a/'*33+'b'):
            self.assertEqual(self.request(file=file)[0],400,file)
        self.assertEqual(self.request(target='/api/skills/sample/files/file?x=1')[0],400)
        self.assertEqual(self.request('binary\0text')[0],400)
        self.assertEqual(self.request('é'*(MAX_BYTES//2+1))[0],413)
        self.assertEqual(self.requests,[])
        self.assertEqual(self.request('a'*MAX_BYTES)[0],200)
        self.assertEqual((self.root/'sample/new.txt').stat().st_size,MAX_BYTES)

    def test_existing_directory_binary_hardlink_symlink_fifo_refused(self):
        for name,body in (('binary',b'a\0b'),('invalid',b'\xff'),('linked',b'safe'),('oversize',b'x'*(MAX_BYTES+1))):
            (self.root/'sample'/name).write_bytes(body)
        os.link(self.root/'sample/linked',self.root/'sample/hard')
        os.symlink('existing',self.root/'sample/linkdir')
        os.symlink('linked',self.root/'sample/linkfile')
        os.mkfifo(self.root/'sample/pipe')
        for file in ('binary','invalid','hard','linkdir','linkdir/new','linkfile','pipe','existing','oversize'):
            self.assertEqual(self.request(file=file)[0],400,file)
        self.assertEqual((self.root/'sample/linked').read_text(),'safe')
        self.assertFalse((self.root/'sample/existing/new').exists())

    def test_unsupported_xattr_refuses_without_replacing_original(self):
        path=self.root/'sample/new.txt'; path.write_text('old')
        with patch('artex.tools.filesystem.os.listxattr',return_value=['user.synthetic']):
            status,body,_=self.request()
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertEqual(path.read_text(),'old')
        self.assertEqual(list(self.root.rglob('.artex-write-*')),[])

    def test_target_changed_in_first_grant_cannot_be_overwritten(self):
        path=self.root/'sample/new.txt'; path.write_text('old'); calls=[]
        def mutate(request,proposal):
            if not calls: calls.append(1); path.write_text('newer author')
            return True
        self.make(mutation_authorize=mutate)
        self.assertEqual(self.request()[0],409)
        self.assertEqual(path.read_text(),'newer author')

    def test_target_changed_in_last_primitive_grant_is_not_lost(self):
        path=self.root/'sample/new.txt'; path.write_text('old'); calls=[]
        def mutate(request,proposal):
            calls.append(1)
            # admission, primitive entry, post-temp/pre-version, pre-replace
            if len(calls)==4: path.write_text('newer author')
            return True
        self.make(mutation_authorize=mutate)
        status,body,_=self.request()
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertEqual(len(calls),4); self.assertEqual(path.read_text(),'newer author')
        self.assertEqual(list(self.root.rglob('.artex-write-*')),[])

    def test_named_parent_replacement_at_temp_commit_checkpoint(self):
        calls=[]
        def mutate(request,proposal):
            calls.append(1)
            if len(calls)==3:
                (self.root/'sample/existing').rename(self.root/'sample/moved')
                (self.root/'sample/existing').mkdir()
            return True
        self.make(mutation_authorize=mutate)
        status,body,_=self.request(file='existing/new.txt')
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertFalse((self.root/'sample/existing/new.txt').exists())
        self.assertFalse((self.root/'sample/moved/new.txt').exists())
        self.assertEqual(list(self.root.rglob('.artex-write-*')),[])

    def test_named_root_replacement_first_grant(self):
        import shutil
        moved=self.root.with_name(self.root.name+'-moved'); calls=[]
        def mutate(request,proposal):
            if not calls:
                calls.append(1); self.root.rename(moved); self.root.mkdir(); (self.root/'sample').mkdir()
            return True
        self.make(mutation_authorize=mutate)
        try:
            self.assertEqual(self.request()[0],409)
            self.assertFalse((self.root/'sample/new.txt').exists()); self.assertFalse((moved/'sample/new.txt').exists())
        finally:
            if moved.exists(): shutil.rmtree(self.root); moved.rename(self.root)

    def test_request_and_proposal_tampering_cannot_change_granted_payload(self):
        changes=(lambda r,p: object.__setattr__(r,'body',b'{"content":"injected"}'),
                 lambda r,p: object.__setattr__(p,'content_sha256','false'),
                 lambda r,p: object.__setattr__(p.selection,'path','other'),
                 lambda r,p: object.__setattr__(p,'previous_version',('forged',)))
        for mutate in changes:
            self.make(mutation_authorize=lambda r,p: mutate(r,p) or True)
            self.assertEqual(self.request()[0],403)
        self.assertFalse((self.root/'sample/new.txt').exists()); self.assertFalse((self.root/'sample/other').exists())

    def test_fsync_failure_after_replace_is_unknown_without_retry(self):
        original=os.fsync
        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError('private-error')
            return original(fd)
        with patch('artex.tools.filesystem.os.fsync',side_effect=fsync): status,body,_=self.request()
        self.assertEqual(status,500); self.assertEqual(body['outcome'],'unknown'); self.assertIs(body['retry'],False)
        self.assertEqual((self.root/'sample/new.txt').read_text(),'new text'); self.assertNotIn('private-error',str(body))

    def test_interrupt_after_replace_keeps_effect_and_reports_unknown(self):
        original=os.replace
        def replace(*args,**kwargs): original(*args,**kwargs); raise KeyboardInterrupt()
        with patch('artex.tools.filesystem.os.replace',side_effect=replace): status,body,_=self.request()
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertEqual((self.root/'sample/new.txt').read_text(),'new text')

    def test_revocation_after_replace_and_guard_cleanup_never_claims_rollback(self):
        original=os.replace
        def replace(*args,**kwargs): original(*args,**kwargs); self.current=False
        with patch('artex.tools.filesystem.os.replace',side_effect=replace): status,body,_=self.request()
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertEqual((self.root/'sample/new.txt').read_text(),'new text')
        self.current=True
        @contextmanager
        def guard(*args):
            with self.lock:
                yield
                raise RuntimeError('private guard failure')
        self.make(writer_guard=guard)
        status,body,_=self.request('changed')
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertEqual((self.root/'sample/new.txt').read_text(),'changed')

    def test_post_header_authority_revocation_withholds_body(self):
        for field in ('current','global_grant','mutate','source'):
            def revoke(): setattr(self,field,False)
            with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'): self.request(callback=revoke)
            self.assertEqual((self.root/'sample/new.txt').read_text(),'new text')
            setattr(self,field,True)
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'):
            self.request(callback=lambda:setattr(self.auth,'key',b'z'*32))

    def test_post_header_content_replacement_withholds_body(self):
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'):
            self.request(callback=lambda:(self.root/'sample/new.txt').write_text('other writer'))
        self.assertEqual((self.root/'sample/new.txt').read_text(),'other writer')

    def test_final_guard_exit_revocation_is_rechecked(self):
        exits=[]
        @contextmanager
        def guard(*args):
            with self.lock:
                yield
                exits.append(1)
                if len(exits)==3: self.mutate=False
        self.make(writer_guard=guard)
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'): self.request()
        self.assertEqual(len(exits),3); self.assertEqual((self.root/'sample/new.txt').read_text(),'new text')

    def test_route_conflicts_and_method_or_binding_swaps_refused(self):
        for path in ('/api/skills/sample/files/static','/api/skills/{name}/files/{file}','/api/{kind}/{name}/files/a/b'):
            original=WSGIApplication(self.auth.router()); original.router.add('GET',path,lambda r,p:json_response(200,{}))
            with self.assertRaises(ValueError): bind_skill_content_writes(original,service=self.service,authorize_global=lambda *a:True)
        self.service.handle=lambda *a,**k:json_response(200,{'ok':True})
        with self.assertRaises(ValueError): bind_skill_content_writes(self.original,service=self.service,authorize_global=lambda *a:True)
        self.make(); self.service.mutate=lambda *a:True
        self.assertEqual(self.request()[0],503)
        self.assertFalse((self.root/'sample/new.txt').exists())

    def test_empty_text_and_executable_looking_text_remain_inert(self):
        self.assertEqual(self.request('',file='empty.txt')[0],200)
        self.assertEqual((self.root/'sample/empty.txt').read_bytes(),b'')
        payload='#!/bin/sh\necho should-not-run\n{{.Secret}}'
        self.assertEqual(self.request(payload,file='script.sh')[0],200)
        self.assertEqual((self.root/'sample/script.sh').read_text(),payload)
        self.assertEqual(stat.S_IMODE((self.root/'sample/script.sh').stat().st_mode),0o600)

    def test_scalar_proposal_type_tampering_is_denied(self):
        def mutate(request,proposal): object.__setattr__(proposal,'content_bytes',True); return True
        self.make(mutation_authorize=mutate)
        self.assertEqual(self.request('x')[0],403)
        self.assertFalse((self.root/'sample/new.txt').exists())

    def test_final_primitive_grant_parent_displacement_cannot_commit(self):
        calls=[]
        def mutate(request,proposal):
            calls.append(1)
            if len(calls)==4:
                (self.root/'sample/existing').rename(self.root/'sample/moved')
                (self.root/'sample/existing').mkdir()
            return True
        self.make(mutation_authorize=mutate)
        status,body,_=self.request(file='existing/new.txt')
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertEqual(len(calls),4)
        self.assertFalse((self.root/'sample/existing/new.txt').exists())
        self.assertFalse((self.root/'sample/moved/new.txt').exists())
        self.assertEqual(list(self.root.rglob('.artex-write-*')),[])

    def test_parallel_distinct_writes_use_exclusive_guard_and_finish(self):
        errors=[]; statuses=[]
        def work(index):
            try: statuses.append(self.request(str(index),file=f'{index}.txt')[0])
            except BaseException as exc: errors.append(type(exc).__name__)
        threads=[threading.Thread(target=work,args=(i,)) for i in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3); self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]); self.assertEqual(statuses,[200]*4)
        for i in range(4): self.assertEqual((self.root/f'sample/{i}.txt').read_text(),str(i))

    def test_actual_http_auth_utf8_and_single_uri_decode(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args): pass
        (self.root/'測試').mkdir()
        server=make_server('127.0.0.1',0,self.app,handler_class=Quiet)
        thread=threading.Thread(target=server.serve_forever); thread.start()
        def send(path,auth=True):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'Content-Type':'application/json'}
            if auth: headers['Authorization']='Bearer '+self.token
            connection.request('PUT',path,json.dumps({'content':'café 資料'}),headers)
            response=connection.getresponse(); status=response.status; body=response.read(); connection.close()
            return status,json.loads(body)
        try:
            self.assertEqual(send('/api/skills/sample/files/new.txt',False)[0],401)
            self.assertFalse((self.root/'sample/new.txt').exists())
            self.assertEqual(send('/api/skills/'+quote('測試',safe='')+'/files/'+quote('資料.txt',safe='')),(200,{'ok':True}))
            self.assertEqual((self.root/'測試/資料.txt').read_text(),'café 資料')
            for target,status in (('/api/skills/%2573ample/files/no.txt',400),('/api/skills/sample/files/%252e%252e/out',400),('/api/skills/sample/files/%2e%2e/out',400),('/api/skills/%ff/files/no.txt',400)):
                self.assertEqual(send(target)[0],status,target)
        finally:
            server.shutdown(); thread.join(3); server.server_close()
        self.assertFalse(thread.is_alive())
