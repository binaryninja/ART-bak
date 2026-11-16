"""Independent admission scope negatives, without schema success substitutes."""
import unittest
from copy import deepcopy
from dataclasses import replace
from artex.agent.timed_admission import _validate_graph_catalog,prepare_timed_admission,TimedAdmissionRequest
from artex.db.tool_catalog import CatalogTool
from artex.tools.graph import SCHEMAS
from artex.manager import RuntimeUnavailable
import test_timed_admission_candidate as fixture

class AdmissionScopeReview(unittest.TestCase):
    def rows(self):
        return {role:[CatalogTool(name,True,'native',deepcopy(schema),[role],True,'builtin',{},False)
                for name,schema in SCHEMAS.items()] for role in ('planner','worker')}
    def test_schema_scalar_types_are_exact(self):
        rows=self.rows()
        rows['planner'][0].schema['additionalProperties']=0
        with self.assertRaises(Exception):_validate_graph_catalog(rows)
    def test_duplicate_role_and_external_execution_refused(self):
        rows=self.rows()
        for changed in ([rows['planner'][0]]*2,[replace(rows['planner'][0],agents=['planner','worker'])],
                        [replace(rows['planner'][0],kind='http')],[replace(rows['planner'][0],exec={'url':'https://invalid'})]):
            with self.subTest(changed=changed):
                with self.assertRaises(Exception):_validate_graph_catalog({**rows,'planner':changed})
    def test_generic_executor_refuses_before_reads_or_digest_mutation(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups)
        runtime=f.f.f.runtime;before=runtime._configuration_bytes;calls=list(f.f.f.f.db.calls)
        with self.assertRaisesRegex(RuntimeUnavailable,'Exclusive owned PostgreSQL'):
            prepare_timed_admission(runtime,f.policy,TimedAdmissionRequest('test'))
        self.assertEqual(f.f.f.f.db.calls,calls);self.assertEqual(runtime._configuration_bytes,before)
        self.assertIsNone(runtime._timed_admission)
