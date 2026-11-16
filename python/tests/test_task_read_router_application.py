import unittest
from contextlib import nullcontext
from artex.db.tasks import TaskStore
from artex.server.task_read_builder import TaskReadBuilder
from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
import test_task_runtime_application as fixtures

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.calls=[];e=self.f.f.db
        def forbidden(*a):self.calls.append(a);raise AssertionError('No setup read policy call')
        self.builder=TaskReadBuilder(e,request_authorize=forbidden,context_authorize=forbidden,asset_authorize=forbidden,
            runtime_snapshot=forbidden,activity_authorize=forbidden,activity_raw_authorize=forbidden,
            activity_request_authorize=forbidden,stream_authorize=forbidden)
        self.router=TaskRuntimeReadRouter(TaskStore(e),self.builder,revision='read-r1',authorize_selector=forbidden,current=forbidden,lifecycle_guard=forbidden)
    def test_one_application_no_setup_grants_or_model_calls(self):
        result=self.f.build(read_router=self.router)
        self.assertEqual(self.calls,[]);self.assertEqual(self.f.p.calls,[])
        self.assertIsNot(result.app,result.bootstrap.app)
        self.assertIs(result.manager,result.host.manager)
    def test_foreign_executor_rejected_before_inventory_read(self):
        before=list(self.f.f.db.calls);self.router.tasks.executor=object()
        with self.assertRaises(ValueError):self.f.build(read_router=self.router)
        self.assertEqual(self.f.f.db.calls,before);self.assertEqual(self.calls,[])
    def test_mutually_exclusive_fixed_and_dynamic_before_reads(self):
        before=list(self.f.f.db.calls)
        with self.assertRaises(ValueError):self.f.build(read_router=self.router,read_services=object())
        with self.assertRaises(ValueError):self.f.build(read_router=self.router,application_options={'task_activity_service':object()})
        self.assertEqual(self.f.f.db.calls,before)
    def test_replaced_original_callback_rejected_before_reads(self):
        before=list(self.f.f.db.calls);self.router.authorize_selector=lambda *a:True
        with self.assertRaises(PermissionError):self.f.build(read_router=self.router)
        self.assertEqual(self.f.f.db.calls,before)
