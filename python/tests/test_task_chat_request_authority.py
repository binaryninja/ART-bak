import unittest
from dataclasses import replace
import test_task_chat_http as fixture

class Tests(unittest.TestCase):
    def make(self):
        f=fixture.TaskChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups)
        f.allowed=True;f.service.authorize=lambda req,task:f.allowed
        return f
    def test_prepare_factory_and_transcript_revocation_deny_without_effects(self):
        for seam in ('prepare','factory','transcript'):
            with self.subTest(seam=seam):
                f=self.make()
                if seam=='prepare':
                    original=f.core.provider_resolver
                    def hook(*a):f.allowed=False;return original(*a)
                    f.core.provider_resolver=hook
                elif seam=='factory':
                    original=f.dispatch.activity_store
                    def hook(*a):f.allowed=False;return original(*a)
                    f.dispatch.activity_store=hook
                else:
                    original=f.transcript.check_scoped
                    def hook(*a):f.allowed=False;return original(*a)
                    f.transcript.check_scoped=hook
                response=f.router.dispatch(f.request())
                self.assertEqual(response.status,403);self.assertEqual(f.sink.rows,[])
                self.assertEqual(f.provider.calls,[]);self.assertEqual(f.transcript.store.read('exp9-main'),[])
                self.assertTrue(f.dispatch.close(timeout=1).drained)
    def test_request_and_ambient_selection_pinned(self):
        for kind in ('request','selection'):
            with self.subTest(kind=kind):
                f=self.make();request=f.request(query='');original=f.core.provider_resolver;selected=[1]
                f.service.resolve_task=lambda requested:selected[0]
                def hook(*a):
                    if kind=='request':request.headers['Authorization']='changed'
                    else:selected[0]=2
                    return original(*a)
                f.core.provider_resolver=hook
                self.assertEqual(f.router.dispatch(request).status,403)
                self.assertEqual(f.sink.rows,[]);self.assertEqual(f.provider.calls,[])
    def test_revoke_after_user_persistence_retains_uncertainty_not_false_rollback(self):
        f=self.make();original=f.sink.append_many
        def append(items):
            result=original(items);f.allowed=False;return result
        f.sink.append_many=append
        response=f.router.dispatch(f.request())
        self.assertEqual(response.status,202)
        result=f.dispatch.close(timeout=1)
        self.assertFalse(result.drained);self.assertGreater(result.unresolved,0)
        self.assertEqual([r.kind for r in f.sink.rows],['user']);self.assertEqual(f.provider.calls,[])
    def test_durable_write_then_error_is_uncertain_not_denied(self):
        f=self.make();original=f.sink.append_many
        def append(items):
            original(items);f.allowed=False;raise RuntimeError('private fixture error')
        f.sink.append_many=append
        response=f.router.dispatch(f.request())
        self.assertEqual(response.status,503);self.assertIs(response.json()['persisted'],False)
        self.assertEqual([r.kind for r in f.sink.rows],['user'])
        self.assertFalse(f.dispatch.close(timeout=1).drained);self.assertEqual(f.provider.calls,[])

    def test_explicit_false_ticket_authorizer_never_admits(self):
        f=self.make();scope=f.dispatch.prepare(1)
        with self.assertRaises(PermissionError):
            f.dispatch.submit(scope,'denied','hello',authorize=lambda:False)
        self.assertEqual(f.sink.rows,[]);self.assertEqual(f.provider.calls,[])

    def test_registered_router_authority_revocation_and_replacement(self):
        from artex.server.core import Router
        for mode in ('revoke','replace'):
            with self.subTest(mode=mode):
                f=self.make();allowed=[True];router=Router(lambda req:allowed[0]);f.service.register(router)
                original=f.core.provider_resolver
                def hook(*args):
                    if mode=='revoke':allowed[0]=False
                    else:router.authorize=lambda req:True
                    return original(*args)
                f.core.provider_resolver=hook
                self.assertEqual(router.dispatch(f.request()).status,403)
                self.assertEqual(f.sink.rows,[]);self.assertEqual(f.provider.calls,[])
                self.assertTrue(f.dispatch.close(timeout=1).drained)

    def test_router_expiry_after_acceptance_prevents_provider_and_retains_owner(self):
        from artex.server.core import Router
        f=self.make();allowed=[True];router=Router(lambda req:allowed[0]);f.service.register(router)
        original=f.sink.append_many
        def append(items):
            result=original(items);allowed[0]=False;return result
        f.sink.append_many=append
        self.assertEqual(router.dispatch(f.request()).status,202)
        self.assertFalse(f.dispatch.close(timeout=1).drained)
        self.assertEqual([row.kind for row in f.sink.rows],['user']);self.assertEqual(f.provider.calls,[])

    def test_status_stop_recheck_router_after_selection(self):
        from artex.server.core import Router,Request
        for action in ('status','stop'):
            for mode in ('revoke','replace'):
                with self.subTest(action=action,mode=mode):
                    f=self.make();allowed=[True];router=Router(lambda req:allowed[0]);f.service.register(router)
                    calls=[]
                    f.dispatch.running=lambda task:calls.append('running') or True
                    f.dispatch.cancel=lambda task:calls.append('cancel') or True
                    def resolve(task):
                        if mode=='revoke':allowed[0]=False
                        else:router.authorize=lambda req:True
                        return 1
                    f.service.resolve_task=resolve
                    response=router.dispatch(Request('GET' if action=='status' else 'POST','/api/tasks/1/chat/'+action))
                    self.assertEqual(response.status,403);self.assertEqual(calls,[])

    def test_status_running_callback_revocation_denies_body(self):
        from artex.server.core import Router,Request
        f=self.make();allowed=[True];router=Router(lambda req:allowed[0]);f.service.register(router)
        def running(task):allowed[0]=False;return True
        f.dispatch.running=running
        response=router.dispatch(Request('GET','/api/tasks/1/chat/status'))
        self.assertEqual(response.status,403);self.assertNotIn('running',response.json())

    def test_denied_stop_does_not_cancel_genuine_active_token(self):
        from artex.server.core import Router,Request
        from threading import Event
        f=self.make();allowed=[True];router=Router(lambda req:allowed[0]);f.service.register(router)
        entered=Event();release=Event();f.provider.hook=lambda:(entered.set(),release.wait(3))
        original=f.service.resolve_task
        try:
            self.assertEqual(router.dispatch(f.request()).status,202);self.assertTrue(entered.wait(1))
            with f.core._lock:token=f.core._active[1]
            def resolve(task):allowed[0]=False;return 1
            f.service.resolve_task=resolve
            response=router.dispatch(Request('POST','/api/tasks/1/chat/stop'))
            self.assertEqual(response.status,403);self.assertFalse(token.cancelled)
        finally:
            f.service.resolve_task=original;allowed[0]=True;release.set()
        self.assertTrue(f.dispatch.close(timeout=2).drained)
