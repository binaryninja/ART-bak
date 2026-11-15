"""Independent upload wiring checks with inert handlers, never filesystem access."""
import io
import unittest
from artex.server.application import build_application, build_persisted_application
from artex.server.auth import sign_jwt
from artex.server.core import json_response
from artex.server.workspace_upload import WorkspaceUploadService
from test_application import TestCodec
from test_tasks_store import Fake


class Review(unittest.TestCase):
    def service(self):
        def forbidden(*args):
            raise AssertionError('No workspace access in composition review')
        return WorkspaceUploadService(resolve=forbidden, authorize=forbidden,
            current=forbidden, writer_guard=forbidden, upload_authorize=forbidden)

    def request(self, app, data, auth=True):
        statuses=[]
        env={'REQUEST_METHOD':'POST','PATH_INFO':'/api/workspace/upload','QUERY_STRING':'path=fixture',
             'CONTENT_TYPE':'multipart/form-data; boundary=fixture','CONTENT_LENGTH':str(len(data)),
             'wsgi.input':io.BytesIO(data)}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        list(app(env,lambda s,h:statuses.append(s)))
        return int(statuses[0].split()[0])

    def test_auth_and_body_cap_precede_handler_and_bytes_unchanged(self):
        service=self.service();seen=[]
        def handler(request):
            seen.append((request.body,request.header('Content-Type'),request.target))
            return json_response(200,{'inert':True})
        service.handle=handler
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec(),workspace_upload_service=service,max_body_bytes=32)
        data=b'\x00\xff\r\nunaltered'
        self.assertEqual(self.request(app,data,False),401);self.assertEqual(seen,[])
        self.assertEqual(self.request(app,b'x'*33),413);self.assertEqual(seen,[])
        self.assertEqual(self.request(app,data),200)
        self.assertEqual(seen,[(data,'multipart/form-data; boundary=fixture','/api/workspace/upload?path=fixture')])
        self.assertEqual(db.calls,[])

    def test_invalid_exact_type_rejected_before_persisted_reads(self):
        db=Fake([])
        class Derived(WorkspaceUploadService):pass
        with self.assertRaises(ValueError):
            build_persisted_application(db,b'x'*32,TestCodec(),workspace_upload_service=object.__new__(Derived))
        self.assertEqual(db.calls,[])
