"""Actual temporary POSIX filesystem and JWT/WSGI; no PostgreSQL or user files."""
from contextlib import contextmanager
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
from artex.server.skill_directory_create import SkillDirectoryCreateService, bind_skill_directory_creation
from artex.skills.file_listing import SkillDirectoryScope
from artex.tools.workspace_mutations import WorkspaceMutations
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
        self.service=SkillDirectoryCreateService(self.scope,**options)
        def global_grant(request, scope, selection): self.requests.append(('global',selection)); return self.global_grant
        self.app=bind_skill_directory_creation(self.original,service=self.service,authorize_global=global_grant)

    def request(self, path='new', *, target='/api/skills/sample/dirs', auth=True, method='POST', raw=None, callback=None):
        body=json.dumps({'path':path}).encode() if raw is None else raw
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

    def test_construction_inert_and_original_routes_unchanged(self):
        self.assertEqual(self.requests,[])
        self.assertFalse(any(path=='/api/skills/{name}/dirs' for method,path,*rest in self.original.router._routes))
        self.assertEqual(sorted(p.name for p in (self.root/'sample').iterdir()),['existing'])
        self.assertEqual(self.request(target='/api/health',method='GET',raw=b'')[0],200)

    def test_unauthenticated_and_wrong_method_never_access_filesystem(self):
        with patch.object(WorkspaceMutations,'prepare',side_effect=AssertionError('No prepare')):
            self.assertEqual(self.request(auth=False)[0],401)
            self.assertEqual(self.request(method='GET',raw=b'')[0],405)
        self.assertEqual(self.requests,[])

    def test_actual_nested_directory_source_contract_and_mode(self):
        original=os.mkdir
        def mkdir(*args,**kwargs):
            self.assertGreater(self.guard_depth,0)
            return original(*args,**kwargs)
        with patch('artex.tools.workspace_mutations.os.mkdir',side_effect=mkdir):
            status,body,_=self.request('existing/nested')
        self.assertEqual((status,body),(201,{'dir':'existing/nested'}))
        self.assertEqual(stat.S_IMODE((self.root/'sample/existing/nested').stat().st_mode),0o700)
        proposal=self.proposals[0]
        self.assertIs(proposal.scope,self.scope)
        self.assertEqual((proposal.selection.name,proposal.selection.path,proposal.target.action),('sample','existing/nested','mkdir'))
        self.assertEqual(proposal.target.path,'sample/existing/nested')
        self.assertEqual(proposal.target.kind,'missing')

    def test_existing_directory_is_idempotent_and_not_recreated(self):
        before=(self.root/'sample/existing').stat().st_ino
        with patch('artex.tools.workspace_mutations.os.mkdir',side_effect=AssertionError('No mkdir')):
            self.assertEqual(self.request('existing')[:2],(201,{'dir':'existing'}))
        self.assertEqual((self.root/'sample/existing').stat().st_ino,before)

    def test_no_parent_or_missing_skill_creation(self):
        self.assertEqual(self.request('missing/nested')[0],404)
        self.assertFalse((self.root/'sample/missing').exists())
        self.assertEqual(self.request(target='/api/skills/absent/dirs')[0],404)
        self.assertFalse((self.root/'absent').exists())

    def test_source_global_current_and_mutation_grants_are_independent(self):
        for field in ('source','global_grant','current','mutate'):
            setattr(self,field,False)
            with self.subTest(field=field): self.assertEqual(self.request()[0],403)
            setattr(self,field,True)
            self.assertFalse((self.root/'sample/new').exists())

    def test_strict_body_and_canonical_path_before_any_authority(self):
        for path in ('','.', '..','../escape','a/../b','/tmp/escape','a//b','./x','a\\b','a%2fb','a\x00b','a/' ,'a/'*33+'b'):
            self.assertEqual(self.request(path)[0],400,path)
        for raw in (b'',b'{}',b'[]',b'{"path":null}',b'{"path":"x","path":"y"}',b'{"path":"x","extra":1}',b'NaN',b'x'*4097):
            self.assertEqual(self.request(raw=raw)[0],400)
        self.assertEqual(self.request(target='/api/skills/sample/dirs?path=x')[0],400)
        self.assertEqual(self.requests,[])

    def test_file_symlink_ancestor_hardlink_and_fifo_refused(self):
        (self.root/'sample/file').write_text('fixture')
        (self.root/'sample/linked').write_text('fixture')
        os.link(self.root/'sample/linked',self.root/'sample/hard')
        os.symlink('existing',self.root/'sample/link')
        os.mkfifo(self.root/'sample/pipe')
        for name,status in (('file',409),('hard',400),('link',400),('link/new',400),('pipe',400)):
            self.assertEqual(self.request(name)[0],status)
        self.assertFalse((self.root/'sample/existing/new').exists())

    def test_utf8_skill_and_directory_names(self):
        (self.root/'測試').mkdir()
        self.assertEqual(self.request('資料',target='/api/skills/測試/dirs')[:2],(201,{'dir':'資料'}))
        self.assertTrue((self.root/'測試/資料').is_dir())

    def test_target_replaced_during_mutation_grant_refused(self):
        first=[]
        def mutate(request,proposal):
            if not first:
                first.append(1); (self.root/'sample/new').write_text('replacement')
            return True
        self.make(mutation_authorize=mutate)
        self.assertEqual(self.request()[0],409)
        self.assertEqual((self.root/'sample/new').read_text(),'replacement')

    def test_parent_renamed_during_grant_never_uses_new_parent(self):
        first=[]
        def mutate(request,proposal):
            if not first:
                first.append(1)
                (self.root/'sample/existing').rename(self.root/'sample/old')
                (self.root/'sample/existing').mkdir()
            return True
        self.make(mutation_authorize=mutate)
        self.assertEqual(self.request('existing/new')[0],409)
        self.assertFalse((self.root/'sample/existing/new').exists())
        self.assertFalse((self.root/'sample/old/new').exists())

    def test_parent_renamed_at_second_grant_before_effect_is_refused(self):
        calls=[]
        def mutate(request,proposal):
            calls.append(1)
            if len(calls)==2:
                (self.root/'sample/existing').rename(self.root/'sample/renamed')
                (self.root/'sample/existing').mkdir()
            return True
        self.make(mutation_authorize=mutate)
        self.assertEqual(self.request('existing/new')[0],409)
        self.assertFalse((self.root/'sample/existing/new').exists())
        self.assertFalse((self.root/'sample/renamed/new').exists())

    def test_final_acknowledgement_rechecks_after_guard_exit(self):
        exits=[]
        @contextmanager
        def guard(scope,selection):
            with self.lock:
                self.guard_depth+=1
                try: yield
                finally:
                    self.guard_depth-=1; exits.append(1)
                    if len(exits)==3: self.current=False
        self.make(writer_guard=guard)
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'): self.request()
        self.assertEqual(len(exits),3)
        self.assertFalse(self.current)
        self.assertTrue((self.root/'sample/new').is_dir())

    def test_mutation_grant_revocation_on_final_guard_exit_is_rechecked(self):
        exits=[]
        @contextmanager
        def guard(scope,selection):
            with self.lock:
                self.guard_depth+=1
                try: yield
                finally:
                    self.guard_depth-=1; exits.append(1)
                    if len(exits)==3: self.mutate=False
        self.make(writer_guard=guard)
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'): self.request()
        self.assertTrue((self.root/'sample/new').is_dir())

    def test_scope_and_request_mutation_during_grant_denied(self):
        def mutate(request,proposal): object.__setattr__(request,'body',b'{"path":"other"}'); return True
        self.make(mutation_authorize=mutate)
        self.assertEqual(self.request()[0],403)
        self.assertFalse((self.root/'sample/new').exists())
        self.make(mutation_authorize=lambda request,proposal: object.__setattr__(proposal.target,'action','delete') or True)
        self.assertEqual(self.request()[0],403)
        self.assertTrue((self.root/'sample').exists())

    def test_named_root_replacement_during_grant_refused(self):
        import shutil
        moved=self.root.with_name(self.root.name+'-moved')
        first=[]
        def mutate(request,proposal):
            if not first:
                first.append(1); self.root.rename(moved); self.root.mkdir(); (self.root/'sample').mkdir()
            return True
        self.make(mutation_authorize=mutate)
        try:
            self.assertEqual(self.request()[0],409)
            self.assertFalse((self.root/'sample/new').exists())
            self.assertFalse((moved/'sample/new').exists())
        finally:
            if moved.exists(): shutil.rmtree(self.root); moved.rename(self.root)

    def test_writer_guard_exit_failure_after_effect_is_unknown(self):
        @contextmanager
        def guard(scope,selection):
            self.guard_depth+=1
            try: yield
            finally:
                self.guard_depth-=1
                raise RuntimeError('private guard error')
        self.make(writer_guard=guard)
        status,body,_=self.request()
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertTrue((self.root/'sample/new').is_dir())
        self.assertNotIn('private',str(body))

    def test_fsync_failure_after_mkdir_reports_unknown_no_retry(self):
        with patch('artex.tools.workspace_mutations.os.fsync',side_effect=OSError('private-marker')):
            status,body,_=self.request()
        self.assertEqual(status,500); self.assertEqual(body['outcome'],'unknown'); self.assertIs(body['retry'],False)
        self.assertTrue((self.root/'sample/new').is_dir()); self.assertNotIn('private-marker',str(body))

    def test_post_effect_revocation_is_unknown_not_rollback(self):
        original=os.mkdir
        def mkdir(*args,**kwargs): original(*args,**kwargs); self.current=False
        with patch('artex.tools.workspace_mutations.os.mkdir',side_effect=mkdir): status,body,_=self.request()
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertTrue((self.root/'sample/new').is_dir())

    def test_interrupt_after_mkdir_is_unknown_and_keeps_effect(self):
        original=os.mkdir
        def mkdir(*args,**kwargs): original(*args,**kwargs); raise KeyboardInterrupt()
        with patch('artex.tools.workspace_mutations.os.mkdir',side_effect=mkdir): status,body,_=self.request()
        self.assertEqual((status,body['outcome']),(500,'unknown'))
        self.assertTrue((self.root/'sample/new').is_dir())

    def test_jwt_or_global_revocation_after_headers_withholds_success_body(self):
        for kind in ('jwt','global'):
            self.global_grant=True
            def revoke():
                if kind=='jwt': self.auth.key=b'z'*32
                else: self.global_grant=False
            with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'):
                self.request(path='new_'+kind,callback=revoke)
            self.assertTrue((self.root/('sample/new_'+kind)).is_dir())
            self.auth.key=b'x'*32

    def test_post_header_directory_replacement_withholds_success_body(self):
        def replace():
            (self.root/'sample/new').rename(self.root/'sample/replaced')
            (self.root/'sample/new').mkdir()
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'): self.request(callback=replace)
        self.assertTrue((self.root/'sample/replaced').is_dir())

    def test_concurrent_requests_serialize_and_do_not_duplicate_effect(self):
        results=[]; errors=[]
        def work():
            try: results.append(self.request())
            except BaseException as exc: errors.append(type(exc).__name__)
        threads=[threading.Thread(target=work) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3); self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[]); self.assertEqual([result[0] for result in results],[201]*4)
        self.assertTrue((self.root/'sample/new').is_dir())

    def test_route_conflicts_and_method_substitution_rejected_before_io(self):
        for path in ('/api/skills/specific/dirs','/api/skills/{key}/dirs','/api/{kind}/{name}/dirs'):
            original=WSGIApplication(self.auth.router()); original.router.add('POST',path,lambda r,p:json_response(200,{}))
            with self.assertRaises(ValueError): bind_skill_directory_creation(original,service=self.service,authorize_global=lambda *a:True)
        self.service.handle=lambda *a,**k:json_response(201,{})
        with self.assertRaises(ValueError): bind_skill_directory_creation(self.original,service=self.service,authorize_global=lambda *a:True)
        self.assertEqual(self.requests,[])

    def test_source_binding_swap_after_construction_fails_closed(self):
        self.service.authorize=lambda *a:True
        self.assertEqual(self.request()[0],503)
        self.assertFalse((self.root/'sample/new').exists())

    def test_actual_loopback_http_auth_and_directory_creation(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args): pass
        server=make_server('127.0.0.1',0,self.app,handler_class=Quiet)
        thread=threading.Thread(target=server.serve_forever); thread.start()
        try:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            body=json.dumps({'path':'over_http'})
            connection.request('POST','/api/skills/sample/dirs',body,{'Content-Type':'application/json'})
            response=connection.getresponse(); self.assertEqual(response.status,401); response.read(); connection.close()
            self.assertFalse((self.root/'sample/over_http').exists())
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request('POST','/api/skills/sample/dirs',body,{'Content-Type':'application/json','Authorization':'Bearer '+self.token})
            response=connection.getresponse(); self.assertEqual(response.status,201)
            self.assertEqual(json.loads(response.read()),{'dir':'over_http'}); connection.close()
            self.assertTrue((self.root/'sample/over_http').is_dir())
            (self.root/'測試').mkdir()
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request('POST','/api/skills/'+quote('測試',safe='')+'/dirs',json.dumps({'path':'資料'}),
                               {'Content-Type':'application/json','Authorization':'Bearer '+self.token})
            response=connection.getresponse(); self.assertEqual(response.status,201)
            self.assertEqual(json.loads(response.read()),{'dir':'資料'}); connection.close()
            self.assertTrue((self.root/'測試/資料').is_dir())
            for name,expected in (('%2573ample',400),('sample%252fother',400),('sample%2fother',404),('%ff',400)):
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                connection.request('POST','/api/skills/'+name+'/dirs',json.dumps({'path':'no_alias'}),
                                   {'Content-Type':'application/json','Authorization':'Bearer '+self.token})
                response=connection.getresponse(); self.assertEqual(response.status,expected,name)
                response.read(); connection.close()
            self.assertFalse((self.root/'sample/no_alias').exists())
        finally:
            server.shutdown(); thread.join(3); server.server_close()
        self.assertFalse(thread.is_alive())
