import unittest
from threading import Event,Thread
from artex.agent.runtime import RunStopped
import test_task_chat as core_fixture
import test_task_chat_http as http_fixture

class Tests(unittest.TestCase):
    def test_direct_run_and_prepare_cancel_before_token(self):
        for operation in ('run','prepare'):
            with self.subTest(operation=operation):
                f=core_fixture.TaskChatTests();f.setUp();c=f.core();entered=Event();release=Event();out=[]
                old=c.provider_resolver
                def resolve(*a):entered.set();release.wait(3);return old(*a)
                c.provider_resolver=resolve
                def invoke():
                    try:out.append(c.run(1,'first','hello') if operation=='run' else c.prepare(1))
                    except Exception as error:out.append(error)
                thread=Thread(target=invoke);thread.start()
                try:
                    self.assertTrue(entered.wait(1));self.assertTrue(c.running(1));self.assertTrue(c.cancel(1))
                finally:release.set();thread.join(3)
                self.assertFalse(thread.is_alive());self.assertIsInstance(out[0],RunStopped)
                self.assertEqual(f.provider.calls,[]);self.assertEqual(f.tx.history,{})
                c.provider_resolver=old
                self.assertTrue(c.run(1,'fresh','hello').persisted)
    def make(self):
        f=http_fixture.TaskChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_http_prepare_and_submit_cancel_no_later_effects(self):
        for seam in ('prepare','factory','check_scoped'):
            with self.subTest(seam=seam):
                f=self.make();entered=Event();release=Event();responses=[]
                obj,name=(f.core,'provider_resolver') if seam=='prepare' else (f.dispatch,'activity_store') if seam=='factory' else (f.transcript,'check_scoped')
                old=getattr(obj,name)
                def hook(*a):entered.set();release.wait(3);return old(*a)
                setattr(obj,name,hook)
                thread=Thread(target=lambda:responses.append(f.router.dispatch(f.request())));thread.start()
                try:
                    self.assertTrue(entered.wait(1));self.assertTrue(f.status().json()['running'])
                    self.assertEqual(f.stop().json()['status'],'stopping')
                finally:release.set();thread.join(3);setattr(obj,name,old)
                self.assertFalse(thread.is_alive());self.assertEqual(responses[0].status,503)
                self.assertEqual(f.provider.calls,[]);self.assertEqual(f.sink.rows,[])
                self.assertEqual(f.transcript.store.read('exp9-main'),[])
                req=f.request();req.headers['Idempotency-Key']='fresh'
                self.assertEqual(f.router.dispatch(req).status,202)
                self.assertTrue(f.dispatch.close(timeout=2).drained)
    def test_prepare_submit_gap_uses_same_issued_operation(self):
        f=self.make();old=f.dispatch.prepare;stops=[]
        def prepare(*a,**kw):
            scope=old(*a,**kw);stops.append(f.stop());return scope
        f.dispatch.prepare=prepare
        response=f.router.dispatch(f.request())
        self.assertEqual(stops[0].json()['status'],'stopping');self.assertEqual(response.status,503)
        self.assertEqual(f.sink.rows,[]);self.assertEqual(f.provider.calls,[])
    def test_stop_during_user_write_does_not_claim_rollback(self):
        f=self.make();entered=Event();release=Event();responses=[];old=f.sink.append_many
        def append(items):
            result=old(items)
            if items[0].kind=='user':entered.set();release.wait(3)
            return result
        f.sink.append_many=append
        thread=Thread(target=lambda:responses.append(f.router.dispatch(f.request())));thread.start()
        try:
            self.assertTrue(entered.wait(1));self.assertEqual(f.stop().json()['status'],'stopping')
        finally:release.set();thread.join(3)
        self.assertEqual(responses[0].status,202);self.assertTrue(f.dispatch.close(timeout=2).drained)
        self.assertEqual([r.kind for r in f.sink.rows],['user','result']);self.assertEqual(f.provider.calls,[])
    def test_stop_during_write_then_error_retains_uncertainty(self):
        f=self.make();old=f.sink.append_many;stops=[]
        def append(items):
            old(items);stops.append(f.stop());raise RuntimeError('private write acknowledgement')
        f.sink.append_many=append
        response=f.router.dispatch(f.request())
        self.assertEqual(stops[0].json()['status'],'stopping');self.assertEqual(response.status,503)
        self.assertEqual([r.kind for r in f.sink.rows],['user']);self.assertEqual(f.provider.calls,[])
        self.assertFalse(f.dispatch.close(timeout=1).drained)
    def test_operation_task_mismatch_and_reuse_refused(self):
        f=self.make();scope=f.dispatch.prepare(1)
        with f.dispatch.operation(2) as other:
            with self.assertRaises(Exception):f.dispatch.submit(scope,'bad','hello',operation=other)
        with f.dispatch.operation(1) as expired:pass
        with self.assertRaises(Exception):f.dispatch.submit(scope,'bad','hello',operation=expired)
        self.assertEqual(f.sink.rows,[])
