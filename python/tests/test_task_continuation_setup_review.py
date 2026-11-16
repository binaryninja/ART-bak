"""Independent startup composition tests, no implementation edits."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from artex.server.task_continuation_setup import enable_task_continuation
from artex.agent.host import AgentHost
import test_task_runtime_application as base
import test_task_continuation_setup as owner

class Review(unittest.TestCase):
    def raw(self):
        f=base.Tests();f.setUp();self.addCleanup(f.doCleanups);return f,f.build()
    def test_foreign_pump_bundle_cannot_install_owner(self):
        f,app=self.raw();foreign=replace(app,pump=SimpleNamespace(_thread=None))
        with self.assertRaises(Exception):enable_task_continuation(foreign,authorize=lambda p:True)
        self.assertIsNone(app.manager._continuation_owner)
    def test_replaced_installer_denied_without_invocation(self):
        f,app=self.raw();called=[]
        with patch.object(AgentHost,'install_continuation',lambda *a:called.append(1)):
            with self.assertRaises(ValueError):enable_task_continuation(app,authorize=lambda p:True)
        self.assertFalse(called);self.assertIsNone(app.manager._continuation_owner)
    def test_invalid_bundle_and_unauthorized_route_have_no_effects(self):
        with self.assertRaises(ValueError):enable_task_continuation(object(),authorize=lambda p:True)
        f=owner.Tests();f.setUp();self.addCleanup(f.doCleanups);f.prepare()
        before=list(f.f.f.db.calls)
        self.assertEqual(f.f.request('/api/tasks/5/control',{'action':'pause'},False)[0],401)
        self.assertEqual(f.f.f.db.calls,before);self.assertFalse(f.handle.token.cancelled)
    def test_capacity_one_resume_requires_original_grants_and_replaces_handle(self):
        f=owner.Tests();f.setUp();self.addCleanup(f.doCleanups);f.capacity=1;f.prepare()
        self.assertEqual(f.f.request('/api/tasks/5/control',{'action':'pause'})[0],200)
        before=len(f.grants)
        self.assertEqual(f.f.request('/api/tasks/5/control',{'action':'resume'})[0],200)
        self.assertGreater(len(f.grants),before)
        self.assertTrue(f.handle.token.cancelled)
        new,=f.app.runner.handles();self.assertIsNot(new,f.handle)
        self.assertFalse(f.app.manager._continuation_credits)
    def test_foreign_facade_bundle_cannot_install_owner(self):
        f,app=self.raw()
        hosted=replace(app.bootstrap.hosted,facade=SimpleNamespace(host=app.host))
        foreign=replace(app,bootstrap=replace(app.bootstrap,hosted=hosted))
        with self.assertRaises(Exception):enable_task_continuation(foreign,authorize=lambda p:True)
        self.assertIsNone(app.manager._continuation_owner)
    def test_replaced_original_jwt_cannot_enable_continuation(self):
        f,app=self.raw();app.bootstrap.app.router.authorize=lambda request:True
        with self.assertRaises(Exception):enable_task_continuation(app,authorize=lambda p:True)
        self.assertIsNone(app.manager._continuation_owner)
