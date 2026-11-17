"""Single-app actual Skills filesystem contracts; no SQL or providers."""
from contextlib import contextmanager
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server

from artex.skills import render_skill
from artex.skills.file_listing import SkillDirectoryScope,SkillFileListingReader
from artex.skills.file_read import SkillFileReader
from artex.skills.catalog_read import SkillCatalogReader
from artex.server.auth import AuthService,PASSWORD_KEY
from artex.server.wsgi import WSGIApplication
from artex.server.passwords import BcryptCodec
from artex.server.skill_catalog_read import SkillCatalogReadService
from artex.server.skill_files_read import SkillFilesReadService
from artex.server.skill_content_read import SkillContentReadService
from artex.server.skill_content_write import SkillContentWriteService
from artex.server.skill_directory_create import SkillDirectoryCreateService
from artex.server.skills_workspace_setup import bind_skills_workspace
from artex.server.runtime_listener import QuietRuntimeHandler,_BoundedServer,DevListenerConfig
from test_application import TestCodec


class PasswordStore:
    def __init__(self,value):self.value=value
    def get(self,key):return self.value if key==PASSWORD_KEY else None
    def insert_if_absent(self,*args):return False
    def set(self,key,value):
        if key!=PASSWORD_KEY:raise ValueError()
        self.value=value


class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);(self.root/'sample').mkdir()
        (self.root/'sample/SKILL.md').write_text(render_skill('sample','Synthetic fixture only'))
        info=self.root.stat();self.scope=SkillDirectoryScope(str(self.root),info.st_dev,info.st_ino,'fixture')
        self.lock=threading.RLock();self.open=True;self.calls=[0]*5;self.grants=[True]*5
        @contextmanager
        def guard(*args):
            with self.lock:yield
        self.guard=guard
        current=lambda *args:self.open
        self.reader=SkillFileReader(self.scope,authorize=lambda *args:True,current=current,lifecycle_guard=guard)
        listing=SkillFileListingReader(self.scope,authorize=lambda *args:True,current=current,lifecycle_guard=guard)
        catalog=SkillCatalogReader(self.scope,self.reader,authorize_global=lambda *args:True,current=current,lifecycle_guard=guard)
        self.services=(SkillCatalogReadService(catalog,authorize=lambda *args:True),
            SkillFilesReadService(listing,authorize=lambda *args:True),
            SkillContentReadService(self.reader,authorize=lambda *args:True),
            SkillContentWriteService(self.scope,authorize=lambda *args:True,mutation_authorize=lambda *args:True,current=current,writer_guard=guard),
            SkillDirectoryCreateService(self.scope,authorize=lambda *args:True,mutation_authorize=lambda *args:True,current=current,writer_guard=guard))
        self.password='Synthetic-fixture-password-27'
        self.configure(TestCodec())

    def configure(self,codec):
        self.auth=AuthService(PasswordStore(codec.hash(self.password)),codec,b'x'*32)
        self.original=WSGIApplication(self.auth.router())
        self.bundle=self.bind();self.app=self.bundle.application
        self.token=None

    def bind(self,**changes):
        def grant(index):
            def allow(*args):self.calls[index]+=1;return self.grants[index]
            return allow
        values=dict(zip(('catalog_service','files_service','content_service','write_service','directory_service'),self.services))
        values.update(zip(('authorize_catalog','authorize_files','authorize_content','authorize_write','authorize_directory'),(grant(i) for i in range(5))))
        values.update(changes)
        return bind_skills_workspace(self.original,**values)

    def request(self,path,method='GET',data=None,auth=True,start=None):
        body=b'' if data is None else json.dumps(data).encode()
        env=dict(REQUEST_METHOD=method,PATH_INFO=path.encode().decode('latin-1'),QUERY_STRING='',CONTENT_LENGTH=str(len(body)))
        env['wsgi.input']=io.BytesIO(body)
        if auth and self.token:env['HTTP_AUTHORIZATION']='Bearer '+self.token
        result=[]
        def begin(status,headers):
            result.append((int(status.split()[0]),dict(headers)))
            if start:start()
        iterator=self.app(env,begin)
        try:raw=b''.join(iterator)
        finally:
            if callable(getattr(iterator,'close',None)):iterator.close()
        return result[0][0],json.loads(raw) if raw else None,result[0][1]

    def login(self):
        status,body,_=self.request('/api/auth/login','POST',{'username':'ARTEX','password':self.password},False)
        self.assertEqual(status,200);self.token=body['token']

    def test_inert_same_base_and_exact_shared_scope_and_guard(self):
        self.assertEqual(self.calls,[0]*5)
        self.assertFalse(any('/skills' in route[1] for route in self.original.router._routes))
        for index,view in enumerate(self.app.views[:3]):self.assertIs(view.original,self.original)
        with patch('artex.tools.filesystem.ScopedFilesystem.__init__',side_effect=AssertionError('No IO')):
            self.bind()
        foreign=SkillDirectoryScope(self.scope.root,self.scope.device,self.scope.inode,'different')
        replacement=SkillDirectoryCreateService(foreign,authorize=lambda *a:True,mutation_authorize=lambda *a:True,current=lambda *a:True,writer_guard=self.guard)
        with self.assertRaises(PermissionError):self.bind(directory_service=replacement)
        replacement=SkillDirectoryCreateService(self.scope,authorize=lambda *a:True,mutation_authorize=lambda *a:True,current=lambda *a:True,writer_guard=lambda *a:self.guard())
        with self.assertRaises(PermissionError):self.bind(directory_service=replacement)

    def test_method_routing_authority_and_allow_headers(self):
        paths=(('/api/skills','POST','GET'),('/api/skills/sample/files','PUT','GET, HEAD'),
               ('/api/skills/sample/files/SKILL.md','DELETE','GET, HEAD, PUT'),('/api/skills/sample/dirs','GET','POST'))
        for path,method,allow in paths:self.assertEqual(self.request(path,method)[0],401)
        self.assertEqual(self.calls,[0]*5);self.login()
        for path,method,allow in paths:
            status,body,headers=self.request(path,method)
            self.assertEqual(status,405);self.assertEqual(headers['Allow'],allow)
        self.assertEqual(self.calls,[0]*5)
        self.assertEqual(self.request('/api/health',auth=False)[0],200)
        self.assertEqual(self.request('/api/skills/sample/meta','PUT',{})[0],404)

    def test_independent_family_grants_do_not_enable_writes(self):
        self.login()
        requests=(('/api/skills','GET',None),('/api/skills/sample/files','GET',None),
            ('/api/skills/sample/files/SKILL.md','GET',None),
            ('/api/skills/sample/files/a.txt','PUT',{'content':'fixture'}),
            ('/api/skills/sample/dirs','POST',{'path':'refs'}))
        for index,(path,method,data) in enumerate(requests):
            self.grants[index]=False
            self.assertEqual(self.request(path,method,data)[0],403)
            self.grants[index]=True
        self.assertFalse((self.root/'sample/a.txt').exists());self.assertFalse((self.root/'sample/refs').exists())
        self.grants[3]=self.grants[4]=False
        self.assertEqual(self.request('/api/skills/sample/files/SKILL.md')[0],200)

    def test_create_save_reload_upsert_and_directory_idempotence_in_one_app(self):
        self.login()
        status,catalog,_=self.request('/api/skills');self.assertEqual(status,200)
        self.assertEqual(catalog['skills'][0]['name'],'sample')
        self.assertEqual(self.request('/api/skills/sample/dirs','POST',{'path':'refs'})[:2],(201,{'dir':'refs'}))
        inode=(self.root/'sample/refs').stat().st_ino
        self.assertEqual(self.request('/api/skills/sample/dirs','POST',{'path':'refs'})[0],201)
        self.assertEqual((self.root/'sample/refs').stat().st_ino,inode)
        path='/api/skills/sample/files/refs/a.txt'
        for text in ('new','replacement'):
            self.assertEqual(self.request(path,'PUT',{'content':text})[:2],(200,{'ok':True}))
            self.assertEqual(self.request(path)[:2],(200,{'file':'refs/a.txt','content':text}))
        self.assertEqual(self.request(path,'HEAD')[0],200)
        self.assertIn('refs/a.txt',self.request('/api/skills/sample/files')[1]['files'])
        self.assertIn('refs/a.txt',self.request('/api/skills')[1]['skills'][0]['files'])

    def test_symlink_and_revoked_scope_never_expand_authority(self):
        self.login();outside=self.root/'outside';outside.write_text('outside')
        (self.root/'sample/alias').symlink_to(outside)
        self.assertNotEqual(self.request('/api/skills/sample/files/alias')[0],200)
        self.assertNotEqual(self.request('/api/skills/sample/files/alias','PUT',{'content':'overwrite'})[0],200)
        self.assertEqual(outside.read_text(),'outside')
        self.open=False
        for path,method,data in (('/api/skills/sample/files/SKILL.md','GET',None),('/api/skills/sample/files/a','PUT',{'content':'a'}),('/api/skills/sample/dirs','POST',{'path':'new'})):
            self.assertEqual(self.request(path,method,data)[0],403)
        self.assertFalse((self.root/'sample/a').exists());self.assertFalse((self.root/'sample/new').exists())

    def test_after_headers_mutation_grant_and_binding_changes_withhold_success(self):
        self.login()
        with self.assertRaisesRegex(RuntimeError,'Disclosure unavailable'):
            self.request('/api/skills/sample/files/a','PUT',{'content':'written'},start=lambda:self.grants.__setitem__(3,False))
        self.assertEqual((self.root/'sample/a').read_text(),'written');self.grants[3]=True
        with self.assertRaises(PermissionError):
            self.request('/api/skills/sample/files/a',start=lambda:setattr(self.app,'views',()))

    def test_unknown_write_shape_not_rewritten_or_retried_by_composer(self):
        import os,stat
        self.login();original=os.fsync
        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('private marker')
            return original(fd)
        with patch('artex.tools.filesystem.os.fsync',side_effect=fsync):
            status,body,_=self.request('/api/skills/sample/files/a','PUT',{'content':'written'})
        self.assertEqual(status,500);self.assertEqual(body['outcome'],'unknown');self.assertIs(body['retry'],False)
        self.assertEqual((self.root/'sample/a').read_text(),'written');self.assertNotIn('private marker',str(body))

    def test_actual_hardened_loopback_bcrypt_login_browse_edit_reload(self):
        self.configure(BcryptCodec())
        server=make_server('127.0.0.1',0,self.app,server_class=_BoundedServer,handler_class=QuietRuntimeHandler)
        server.configure(DevListenerConfig(server.server_port,socket_timeout=2),self.original.max_body_bytes)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        def send(path,method='GET',data=None,token=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers={'Content-Type':'application/json'}
            if token:headers['Authorization']='Bearer '+token
            conn.request(method,path,None if data is None else json.dumps(data),headers)
            response=conn.getresponse();status=response.status;body=response.read();conn.close()
            return status,json.loads(body) if body else None
        try:
            self.assertEqual(send('/api/skills')[0],401)
            status,auth=send('/api/auth/login','POST',{'username':'ARTEX','password':self.password});self.assertEqual(status,200)
            token=auth['token'];self.assertEqual(send('/api/skills',token=token)[0],200)
            self.assertEqual(send('/api/skills/sample/dirs','POST',{'path':'refs'},token),(201,{'dir':'refs'}))
            path='/api/skills/sample/files/refs/hello.txt'
            self.assertEqual(send(path,'PUT',{'content':'inert content'},token),(200,{'ok':True}))
            self.assertEqual(send(path,token=token),(200,{'content':'inert content','file':'refs/hello.txt'}))
            self.assertEqual(send(path,'PUT',{'content':'updated'},token),(200,{'ok':True}))
            self.assertEqual(send(path,token=token)[1]['content'],'updated')
            self.assertIn('refs/hello.txt',send('/api/skills',token=token)[1]['skills'][0]['files'])
            self.assertEqual(send(path,'HEAD',token=token),(200,None))
        finally:
            server.shutdown();thread.join(3);self.assertFalse(thread.is_alive())
            self.assertTrue(server.quiesce(3));server.server_close()

    def test_duplicate_base_route_refuses_without_partial_mount(self):
        self.original.router.add('GET','/api/skills/sample/files/static',lambda *a:None)
        before=tuple(self.original.router._routes)
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(tuple(self.original.router._routes),before)
        self.assertEqual(self.calls,[0]*5)

    def test_mutated_delegate_or_source_binding_fails_closed(self):
        self.login()
        self.app.views[3].router.dispatch=lambda request:None
        self.assertEqual(self.request('/api/skills/sample/files/a','PUT',{'content':'never'})[0],503)
        self.assertFalse((self.root/'sample/a').exists())
        self.bundle=self.bind();self.app=self.bundle.application
        self.services[4].mutate=lambda *a:True
        self.assertEqual(self.request('/api/skills/sample/dirs','POST',{'path':'never'})[0],503)
        self.assertFalse((self.root/'sample/never').exists())

    def test_latest_in_request_file_version_is_not_silently_overwritten(self):
        path=self.root/'sample/notes.txt';path.write_text('old');calls=[]
        def mutate(request,proposal):
            calls.append(1)
            if len(calls)==4:path.write_text('newer outside observation')
            return True
        replacement=SkillContentWriteService(self.scope,authorize=lambda *a:True,mutation_authorize=mutate,
            current=lambda *a:self.open,writer_guard=self.guard)
        self.bundle=self.bind(write_service=replacement);self.app=self.bundle.application;self.login()
        status,body,_=self.request('/api/skills/sample/files/notes.txt','PUT',{'content':'stale request'})
        self.assertEqual(status,500);self.assertEqual(body['outcome'],'unknown')
        self.assertEqual(path.read_text(),'newer outside observation')
        self.assertEqual(self.request('/api/skills/sample/files/notes.txt')[1]['content'],'newer outside observation')
