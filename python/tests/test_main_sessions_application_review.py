"""Independent optional main-session composition, inert fake database only."""
import unittest
import test_main_sessions_application as fixture
from artex.server.application import build_application,build_persisted_application
from test_application import TestCodec
from test_tasks_store import Fake
class Review(unittest.TestCase):
    def test_private_original_resource_mismatch_before_persisted_read(self):
        for field in ('_executor','_scope','_guard'):
            f=fixture.MainSessionApplicationTests().make([])
            setattr(f.store,field,Fake([]) if field=='_executor' else object())
            with self.assertRaises(ValueError):build_persisted_application(f.db,b'x'*32,TestCodec(),main_sessions_service=f.service)
            self.assertEqual(f.db.calls,[])
    def test_authorized_exact_selector_positive_and_no_mutation_route(self):
        f=fixture.MainSessionApplicationTests().make([[(5,)],[]])
        app=build_application(f.db,b'x'*32,TestCodec(),main_sessions_service=f.service)
        def request(unused,app,path,method='GET'):
            import io,json
            from urllib.parse import urlsplit
            from artex.server.auth import sign_jwt
            parsed=urlsplit(path);statuses=[]
            env={'REQUEST_METHOD':method,'PATH_INFO':parsed.path,'QUERY_STRING':parsed.query,'wsgi.input':io.BytesIO(b''),'CONTENT_LENGTH':'0','HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
            body=b''.join(app(env,lambda status,headers:statuses.append(status)))
            return int(statuses[0].split()[0]),json.loads(body)
        status,data=request(self,app,'/api/exploration/main-sessions?task=5')
        self.assertEqual(status,200);self.assertEqual(data['current'],0)
        self.assertEqual(data['sessions'][0]['created_at'],'0001-01-01T00:00:00Z')
        self.assertIn(request(self,app,'/api/exploration/main-sessions?task=5',method='POST')[0],(404,405))
