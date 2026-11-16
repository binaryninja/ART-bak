"""Exact existing-authentication identity must survive final read projection."""
import unittest,io,json
from unittest.mock import patch
import test_task_runtime_reads as fixture
from artex.server.application import build_application
from artex.server.auth import sign_jwt
from artex.server import stats,exploration,profiles
from test_application import TestCodec

class FixedAuthenticationReview(unittest.TestCase):
    def test_authentication_replacement_during_json_cannot_release_response(self):
        for module,path,query in ((stats,'/api/stats','task=5'),(exploration,'/api/exploration/intents','task=5'),(profiles,'/api/llm/profiles','')):
            with self.subTest(path=path):
                f=fixture.Tests();f.setUp()
                try:
                    base=build_application(f.reads.executor,b'x'*32,TestCodec())
                    app=f.reads.bind(base);original=module.json_response
                    def serialize(*a,**kw):
                        response=original(*a,**kw);base.router.authorize=lambda req:True
                        return response
                    statuses=[]
                    env={'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':query,'CONTENT_LENGTH':'0',
                         'wsgi.input':io.BytesIO(),'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
                    with patch.object(module,'json_response',serialize):
                        b''.join(app(env,lambda s,h:statuses.append(s)))
                    self.assertFalse(statuses[0].startswith('200'),statuses)
                finally:f.doCleanups()

    def test_task_and_findings_final_projection_authentication(self):
        from artex.server import tasks,task_findings
        import test_task_findings as finding
        from artex.server.task_runtime_reads import TaskRuntimeReads,TaskReadScope
        from contextlib import nullcontext
        for kind in ('task','findings'):
            with self.subTest(kind=kind):
                if kind=='task':
                    f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups)
                    f.f.build();base=f.f.bundle.app;reads=f.reads
                    module=tasks;path='/api/tasks/5';query=''
                else:
                    f=finding.Tests();f.make();base=build_application(f.f.db,b'x'*32,TestCodec())
                    reads=TaskRuntimeReads(f.f.db,TaskReadScope(5,9,'r1'),authorize=lambda *a:True,
                        current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),runtime_snapshot=lambda s:None,
                        findings_service=f.service)
                    module=task_findings;path='/api/exploration/findings';query='task=5'
                app=reads.bind(base);original=module.json_response;called=[]
                def serialize(*a,**kw):
                    response=original(*a,**kw);called.append(True);base.router.authorize=lambda req:True
                    return response
                statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':query,'CONTENT_LENGTH':'0',
                    'wsgi.input':io.BytesIO(),'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
                with patch.object(module,'json_response',serialize):b''.join(app(env,lambda s,h:statuses.append(s)))
                self.assertTrue(called)
                self.assertFalse(statuses[0].startswith('200'),statuses)
