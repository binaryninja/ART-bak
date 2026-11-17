"""Real WSGI URI transport and HEAD parity, not direct Unicode PATH_INFO fiction."""
from contextlib import contextmanager
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote
from wsgiref.simple_server import make_server, WSGIRequestHandler

from artex.server.auth import AuthService, sign_jwt
from artex.server.core import Request
from artex.server.wsgi import WSGIApplication
from artex.server.skill_files_read import SkillFilesReadService
from artex.server.skill_content_read import SkillContentReadService
from artex.server.skill_files_setup import bind_skill_files_reads
from artex.server.skill_content_setup import bind_skill_content_reads
from artex.skills.file_listing import SkillDirectoryScope, SkillFileListingReader
from artex.skills.file_read import SkillFileReader
from test_application import TestCodec


class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        for skill,file in (('sample','notes.txt'),('測試','資料.txt')):
            (self.root/skill).mkdir(); (self.root/skill/file).write_text('inert café content')
        st=self.root.stat(); self.scope=SkillDirectoryScope(str(self.root),st.st_dev,st.st_ino,'fixture')
        self.current=self.source=self.request_allow=self.global_allow=True
        self.calls=[]; self.lock=threading.RLock()
        @contextmanager
        def guard(*args):
            with self.lock: yield
        self.listing=SkillFileListingReader(self.scope,authorize=lambda *a:self.source,current=lambda *a:self.current,lifecycle_guard=guard)
        self.reader=SkillFileReader(self.scope,authorize=lambda *a:self.source,current=lambda *a:self.current,lifecycle_guard=guard)
        def request_grant(req,scope,selection):
            self.calls.append((req.method,req.target,selection)); return self.request_allow
        self.files=SkillFilesReadService(self.listing,authorize=request_grant)
        self.content=SkillContentReadService(self.reader,authorize=request_grant)
        self.now=[1000.]
        self.auth=AuthService(object(),TestCodec(),b'x'*32,clock=lambda:self.now[0]); self.token=sign_jwt(b'x'*32,now=1000.)
        self.original=WSGIApplication(self.auth.router())
        self.apps=(bind_skill_files_reads(self.original,files_service=self.files,authorize_global=lambda *a:self.global_allow).application,
                   bind_skill_content_reads(self.original,content_service=self.content,authorize_global=lambda *a:self.global_allow).application)

    def request(self,index,path,method='GET',auth=True,start=None):
        env={'REQUEST_METHOD':method,'PATH_INFO':path.encode().decode('latin-1'),'QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if auth: env['HTTP_AUTHORIZATION']='Bearer '+self.token
        result=[]
        def headers(status,values):
            result.append((int(status.split()[0]),dict(values)))
            if start: start()
        iterator=self.apps[index](env,headers)
        try: body=b''.join(iterator)
        finally:
            if hasattr(iterator,'close'): iterator.close()
        return result[0][0],body,result[0][1]

    def test_real_percent_encoded_unicode_get_and_head(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args): pass
        for index,suffix in ((0,''),(1,'/'+quote('資料.txt',safe=''))):
            server=make_server('127.0.0.1',0,self.apps[index],handler_class=Quiet)
            thread=threading.Thread(target=server.serve_forever); thread.start()
            def send(path,method='GET',auth=True):
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                headers={'Authorization':'Bearer '+self.token} if auth else {}
                connection.request(method,path,headers=headers)
                response=connection.getresponse(); result=(response.status,response.read(),dict(response.getheaders())); connection.close()
                return result
            try:
                path='/api/skills/'+quote('測試',safe='')+'/files'+suffix
                self.assertEqual(send(path,auth=False)[0],401)
                status,body,headers=send(path)
                self.assertEqual(status,200)
                self.assertEqual(json.loads(body),{'files':['資料.txt']} if index==0 else {'content':'inert café content','file':'資料.txt'})
                head_status,head_body,head_headers=send(path,'HEAD')
                self.assertEqual((head_status,head_body),(200,b''))
                self.assertEqual(head_headers['Content-Length'],str(len(body)))
                self.assertEqual(head_headers['Content-Type'],headers['Content-Type'])
                self.assertEqual(head_headers['X-Content-Type-Options'],'nosniff')
                for bad in ('%2573ample','%ff','sample%252fother'):
                    self.assertEqual(send('/api/skills/'+bad+'/files'+('/notes.txt' if index else ''))[0],400,bad)
                if index:
                    (self.root/'測試/nested').mkdir(); (self.root/'測試/nested/資料.txt').write_text('inert nested')
                    nested='/api/skills/'+quote('測試',safe='')+'/files/'+quote('nested/資料.txt',safe='')
                    nested_status,nested_body,_=send(nested)
                    self.assertEqual(nested_status,200)
                    self.assertEqual(json.loads(nested_body),{'content':'inert nested','file':'nested/資料.txt'})
                    self.assertEqual(send('/api/skills/sample/files/%256eotes.txt')[0],400)
                    self.assertEqual(send('/api/skills/sample/files/%252e%252e/outside')[0],400)
            finally:
                server.shutdown(); thread.join(3); server.server_close()
            self.assertFalse(thread.is_alive())
        self.assertTrue(any(method=='HEAD' and target=='/api/skills/測試/files/資料.txt' for method,target,_ in self.calls))
        self.assertTrue(all('æ' not in target and '%' not in target for _,target,_ in self.calls))

    def test_head_go_shape_preserves_original_method_in_all_grants(self):
        for index,path in ((0,'/api/skills/sample/files'),(1,'/api/skills/sample/files/notes.txt')):
            get=self.request(index,path); self.calls.clear()
            head=self.request(index,path,'HEAD')
            self.assertEqual(head[:2],(200,b'')); self.assertEqual(head[2]['Content-Length'],str(len(get[1])))
            self.assertTrue(self.calls); self.assertTrue(all(method=='HEAD' for method,_,_ in self.calls))

    def test_head_all_independent_grants_and_jwt_before_filesystem(self):
        for index,path in ((0,'/api/skills/測試/files'),(1,'/api/skills/測試/files/資料.txt')):
            with patch('artex.skills.file_listing.ScopedFilesystem',side_effect=AssertionError('No list open')), patch('artex.skills.file_read.ScopedFilesystem',side_effect=AssertionError('No content open')):
                self.assertEqual(self.request(index,path,'HEAD',False)[:2],(401,b''))
                for field in ('source','request_allow','global_allow','current'):
                    setattr(self,field,False)
                    self.assertEqual(self.request(index,path,'HEAD')[:2],(403,b''),field)
                    setattr(self,field,True)

    def test_head_post_header_revocation_still_checks_disclosure(self):
        for index,path in ((0,'/api/skills/測試/files'),(1,'/api/skills/測試/files/資料.txt')):
            for field in ('source','request_allow','global_allow','current'):
                with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
                    self.request(index,path,'HEAD',start=lambda:setattr(self,field,False))
                setattr(self,field,True)

    def test_head_missing_and_invalid_are_bodyless_and_never_success(self):
        for index,path in ((0,'/api/skills/missing/files'),(1,'/api/skills/sample/files/missing')):
            self.assertEqual(self.request(index,path,'HEAD')[:2],(404,b''))
            alias=path.replace('/missing','/%6dissing')
            self.assertEqual(self.request(index,alias,'HEAD')[:2],(400,b''))

    def test_standalone_encoded_request_contract_is_not_changed(self):
        for service,path,expected in ((self.files,'/api/skills/%E6%B8%AC%E8%A9%A6/files',{'files':['資料.txt']}),
            (self.content,'/api/skills/%E6%B8%AC%E8%A9%A6/files/%E8%B3%87%E6%96%99.txt',{'content':'inert café content','file':'資料.txt'})):
            response=service.handle(Request('GET',path,{},b''))
            self.assertEqual(response.status,200); self.assertEqual(response.json(),expected)

    def test_head_jwt_expiry_after_headers_fails_disclosure(self):
        for index,path in ((0,'/api/skills/測試/files'),(1,'/api/skills/測試/files/資料.txt')):
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
                self.request(index,path,'HEAD',start=lambda:self.now.__setitem__(0,1000000.))
            self.now[0]=1000.

    def test_decoder_replacement_before_binding_or_request_fails_closed(self):
        from artex.server import skill_read_transport as decoder
        with patch.object(decoder,'decode_skill_request',lambda request:request):
            with self.assertRaises(PermissionError):
                bind_skill_files_reads(self.original,files_service=self.files,authorize_global=lambda *a:True)
            with self.assertRaises(PermissionError):
                bind_skill_content_reads(self.original,content_service=self.content,authorize_global=lambda *a:True)
            for index,path in ((0,'/api/skills/sample/files'),(1,'/api/skills/sample/files/notes.txt')):
                self.assertEqual(self.request(index,path)[0],503)
        self.assertEqual(self.calls,[])

    def test_decoder_replacement_after_headers_fails_disclosure(self):
        from artex.server import skill_read_transport as decoder
        original=decoder.decode_skill_request
        for index,path in ((0,'/api/skills/測試/files'),(1,'/api/skills/測試/files/資料.txt')):
            try:
                with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
                    self.request(index,path,'HEAD',start=lambda:setattr(decoder,'decode_skill_request',lambda request:request))
            finally: decoder.decode_skill_request=original

    def test_head_pre_header_revocation_fallback_remains_bodyless(self):
        # These cutoffs revoke in outer disclose(), after inner WSGI has already
        # suppressed the successful HEAD body. Its 403 fallback must do so too.
        for index,cutoff,path in ((0,10,'/api/skills/sample/files'),(1,7,'/api/skills/sample/files/notes.txt')):
            calls=[]
            def grant(*args): calls.append(1); return len(calls)<cutoff
            if index==0:
                app=bind_skill_files_reads(self.original,files_service=self.files,authorize_global=grant).application
            else:
                app=bind_skill_content_reads(self.original,content_service=self.content,authorize_global=grant).application
            self.apps=tuple(app if i==index else value for i,value in enumerate(self.apps))
            status,body,headers=self.request(index,path,'HEAD')
            self.assertEqual(status,403); self.assertEqual(body,b'')
            self.assertEqual(len(calls),cutoff)
            expected={'error':'Skill file listing denied' if index==0 else 'Skill content denied'}
            from artex.server.core import json_response
            self.assertEqual(headers['Content-Length'],str(len(json_response(403,expected).body)))
