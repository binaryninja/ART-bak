"""Candidate fails closed until actual source schema validation is available."""
import unittest,sys
from unittest.mock import patch
from dataclasses import replace
from artex.agent.timed_admission import TimedAdmissionRequest,PreparedTimedAdmission
from artex.agent.final_task_provider import FinalTaskProviderAdapter
from artex.agent.owned_finalization import FinalizationPlan
from artex.agent.final_assessment import CATALOG_DIGEST
from artex.db.final_assessment import FinalConfiguration
from artex.agent.runtime import Limits,Grant
from artex.manager import RuntimeUnavailable
import test_timed_lifecycle as base

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=base.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        runtime=self.f.f.runtime
        selected=runtime.providers.select({'provisional':True,'options':{'llm_profile_ids':[2]}},'planner').model
        config=FinalConfiguration(selected,'Explicit final assessment','final-v1',CATALOG_DIGEST,Limits())
        self.plan=FinalizationPlan(config,runtime.transcript,lambda *a:True,lambda *a:True,None,
            lambda *a:Grant(True,True,True),lambda *a:True,lambda *a:True,lambda *a:True,
            self.f.policy.timing_policy.clock,task_provider=FinalTaskProviderAdapter(runtime.providers))
        self.policy=replace(self.f.policy,final_plan=self.plan)
    def test_missing_source_contract_before_runtime_binding_or_inventory(self):
        runtime=self.f.f.runtime;before=list(self.f.f.f.db.calls);digest=runtime._configuration_bytes
        with patch.dict(sys.modules,{'artex.db.timed_source_schema':None}):
            with self.assertRaisesRegex(RuntimeUnavailable,'source schema'):
                self.f.f.build(timed_lifecycle_policy=self.policy,track_manual_actions=True,
                    timed_admission_request=TimedAdmissionRequest('explicit-test-db'))
        self.assertEqual(self.f.f.f.db.calls,before)
        self.assertIsNone(runtime._timed_admission);self.assertEqual(runtime._configuration_bytes,digest)
        self.assertEqual(self.f.f.p.calls,[])
    def test_request_cannot_enable_automatic_mode(self):
        for database,mode in (('', 'manual-observer'),('test','automatic'),(True,'manual-observer')):
            with self.assertRaises(ValueError):TimedAdmissionRequest(database,mode)
    def test_no_arbitrary_readiness_capability(self):
        with self.assertRaises(ValueError):PreparedTimedAdmission(object(),None,None,None,None,None)
        with self.assertRaises(RuntimeUnavailable):self.f.f.runtime.bind_timed_admission(object())
    def test_legacy_final_callback_not_enough_for_admission(self):
        legacy=replace(self.plan,task_provider=None,provider_resolver=lambda *a:None)
        before=list(self.f.f.f.db.calls)
        with self.assertRaises(RuntimeUnavailable):
            self.f.f.build(timed_lifecycle_policy=replace(self.policy,final_plan=legacy),track_manual_actions=True,
                timed_admission_request=TimedAdmissionRequest('explicit-test-db'))
        self.assertEqual(self.f.f.f.db.calls,before)
    def test_tracking_and_lifecycle_required(self):
        before=list(self.f.f.f.db.calls)
        with self.assertRaises(ValueError):self.f.f.build(timed_admission_request=TimedAdmissionRequest('test'))
        self.assertEqual(self.f.f.f.db.calls,before)
    def test_default_application_preserves_rejection(self):
        app=self.f.f.build()
        self.assertIsNone(app.timed_admission)
        with self.assertRaises(RuntimeUnavailable):app.runner.validate_create('inert','goal',{'timeout_seconds':10})
        self.assertEqual(self.f.f.p.calls,[])

    def test_native_catalog_allowlist_does_not_install_or_grant(self):
        from artex.agent.timed_admission import _validate_graph_catalog
        from artex.db.tool_catalog import CatalogTool
        from artex.tools.graph import SCHEMAS
        from copy import deepcopy
        rows={role:[CatalogTool(name,True,'Explicit native graph tool',deepcopy(schema),[role],True,'builtin',{},False)
                    for name,schema in SCHEMAS.items()] for role in ('planner','worker')}
        before=list(self.f.f.f.db.calls)
        _validate_graph_catalog(rows)
        self.assertEqual(self.f.f.f.db.calls,before)
        self.assertEqual(self.f.f.p.calls,[])
        for change in ({'key':'Bash'},{'agents':['worker']},{'enabled':False},{'deferred':True},{'exec':{'command':'x'}},{'schema':{'type':'object'}}):
            bad={**rows,'planner':[replace(rows['planner'][0],**change)]}
            with self.assertRaises(Exception):_validate_graph_catalog(bad)

    def test_replaced_validator_is_not_issuance_authority(self):
        from artex.agent.timed_admission import prepare_timed_admission
        import artex.db.timed_schema as schema
        original=self.f.f.runtime._configuration_bytes
        calls=[]
        with patch.object(schema,'validate_timed_schema',lambda *a,**k:calls.append(1) or True):
            with self.assertRaises(RuntimeUnavailable):prepare_timed_admission(self.f.f.runtime,self.policy,TimedAdmissionRequest('test'))
        self.assertEqual(calls,[])
        self.assertIsNone(self.f.f.runtime._timed_admission)
        self.assertEqual(self.f.f.runtime._configuration_bytes,original)
