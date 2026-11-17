"""Code-owned development assembly, synthetic SQL/providers only."""
from contextlib import nullcontext
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.runtime_config_check import ConfigInvalid
from artex.server.development_setup import prepare_development_runtime, DevelopmentAssemblyFailed
from artex.server.runtime_listener import RuntimeResourceOwner
from artex.server.task_runtime_reads import TaskReadScope
import test_runtime_listener as fixtures
from test_application import TestCodec


def tuning():
    return json.dumps({'version':1,'listener':{'host':'127.0.0.1','port':8989,'max_connections':3,'socket_timeout':2},
        'pump':{'interval':.2,'max_per_tick':7},'reads':{'max_rows':13,'max_bytes':8192}}).encode()


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.lease=RuntimeLease(str(Path(self.tmp.name)/'runtime.lock'));self.lease.acquire()
        self.addCleanup(self.lease.release)
        self.policies=dict(request_authorize=lambda *a:True,context_authorize=lambda *a:True,
            asset_authorize=lambda *a:True,runtime_snapshot=lambda *a:None,activity_authorize=lambda *a:True,
            activity_raw_authorize=lambda *a:True,activity_request_authorize=lambda *a:True,stream_authorize=lambda *a:True)
        self.args=dict(signing_key=b'x'*32,password_codec=TestCodec(),live_last_activity=lambda _:0,
            profile_available=lambda _:True,read_revision='read-v1',authorize_selector=lambda *a:True,
            read_current=lambda *a:True,read_guard=lambda *a:nullcontext(),read_policies=self.policies,single_process=True)
    def build(self, data=None, **changes):
        args=dict(self.args);args.update(changes)
        return prepare_development_runtime(self.f.bundle.runtime,self.f.owner,self.lease,tuning() if data is None else data,**args)
    def cleanup_failed(self,assembly):
        # These tests fail before any listener/pump is started; their synthetic
        # stores own no external resources. Explicit test-only reconciliation.
        self.lease.release(owner=assembly)
    def test_exact_tuning_reaches_live_components_without_start(self):
        with patch('artex.server.runtime_listener.make_server') as server:
            assembly=self.build();self.addCleanup(assembly.session.stop,1)
        server.assert_not_called()
        self.assertIs(assembly.application.runtime,self.f.bundle.runtime)
        self.assertIs(assembly.reads.executor,self.f.pool)
        self.assertIs(assembly.router.builder,assembly.reads)
        self.assertEqual(assembly.reads._bounds[:2],(13,8192))
        services=assembly.reads.build(TaskReadScope(5,9,'read-v1'),current=lambda _:True,lifecycle_guard=lambda _:nullcontext())
        self.assertEqual((services.activity.store.max_rows,services.activity.store.max_bytes),(13,8192))
        self.assertEqual(services.reads.max_bytes,8192)
        self.assertEqual((assembly.application.pump.interval,assembly.application.pump.max_per_tick),(.2,7))
        self.assertEqual((assembly.listener._config.port,assembly.listener._config.max_connections,assembly.listener._config.socket_timeout),(8989,3,2.0))
        self.assertIsNone(assembly.application.pump._thread)
        self.assertEqual(self.f.calls,[]);self.assertEqual(self.f.f.p.calls,[])
        with self.assertRaises(LeaseUnavailable):self.lease.release(owner=assembly)
        self.assertTrue(assembly.session.stop(1).stopped)
    def test_invalid_json_and_overrides_precede_sql(self):
        before=list(self.f.f.f.db.calls)
        with self.assertRaises(ConfigInvalid):self.build(b'{"factory":"untrusted.module"}')
        with self.assertRaises(ValueError):self.build(read_policies=dict(self.policies,max_rows=1))
        with self.assertRaises(ValueError):self.build(single_process=False)
        self.assertEqual(self.f.f.f.db.calls,before);self.assertTrue(self.lease.validate())
    def test_frontend_origin_is_explicit_authority_not_json_tuning(self):
        before=list(self.f.f.f.db.calls)
        with self.assertRaises(ValueError):self.build(frontend_origin='http://untrusted.invalid:3000')
        data=json.loads(tuning());data['listener']['frontend_origin']='http://localhost:3000'
        with self.assertRaises(ConfigInvalid):self.build(json.dumps(data).encode())
        self.assertEqual(self.f.f.f.db.calls,before);self.assertTrue(self.lease.validate())
        assembly=self.build(frontend_origin='http://localhost:3000');self.addCleanup(assembly.session.stop,1)
        self.assertEqual(assembly.listener._config.frontend_origin,'http://localhost:3000')
        self.assertIsNone(assembly.application.pump._thread);self.assertEqual(self.f.calls,[])
    def test_claimed_lease_rejected_before_assembly(self):
        owner=object();self.lease.claim(owner)
        try:
            with patch('artex.server.development_setup.TaskReadBuilder') as builder:
                with self.assertRaises(LeaseUnavailable):self.build()
            builder.assert_not_called()
        finally:self.lease.release(owner=owner)
    def test_failed_assembly_retains_claim_and_partial_handles(self):
        with patch('artex.server.development_setup.RuntimeListener',side_effect=RuntimeError('private detail')):
            with self.assertRaises(DevelopmentAssemblyFailed) as caught:self.build()
        assembly=caught.exception.assembly;self.addCleanup(self.cleanup_failed,assembly)
        self.assertIsNotNone(assembly.application);self.assertIsNotNone(assembly.reads)
        self.assertIsNone(assembly.listener);self.assertIsNone(assembly.session)
        self.assertIsNone(assembly.application.pump._thread)
        self.assertNotIn('private detail',str(caught.exception))
        with self.assertRaises(LeaseUnavailable):self.lease.release()
        self.assertTrue(self.lease.validate())
    def test_missing_policy_never_builds_application(self):
        with patch('artex.server.development_setup.build_task_runtime_application') as app:
            with self.assertRaises(DevelopmentAssemblyFailed) as caught:self.build(read_policies={})
        self.addCleanup(self.cleanup_failed,caught.exception.assembly)
        app.assert_not_called();self.assertIsNone(caught.exception.assembly.reads)
    def test_transfer_requires_exact_owner(self):
        holder=object();next_holder=object();self.lease.claim(holder)
        try:
            with self.assertRaises(LeaseUnavailable):self.lease.transfer(object(),next_holder)
            self.lease.transfer(holder,next_holder)
            with self.assertRaises(LeaseUnavailable):self.lease.release(owner=holder)
        finally:self.lease.release(owner=next_holder)
