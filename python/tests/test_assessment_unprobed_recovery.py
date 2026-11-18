import unittest
from contextlib import contextmanager
from artex.assessment_server import reconcile_unprobed
from artex.assessments.definitions import PUBLIC_REST

class RecoveryTests(unittest.TestCase):
    def pool(self,*,probe=None,tool='run_assessment',status='completed'):
        class Pool:
            writes=[]
            @contextmanager
            def transaction(self):yield self
            def fetch_one(self,q):return ('a',PUBLIC_REST.key,PUBLIC_REST.version,None,3,'running',4)
            def fetch_all(self,q):
                if 'artex_tool_calls' in q.sql:return [(tool,status,4)]
                values={name:{'status':200} for name in PUBLIC_REST.operations}
                values['public_rest_source']={'status':0,'error':'transport_unavailable'}
                values['public_rest_probes']=probe or {'status':0,'error':'verified_source_plan_unavailable','requests':[]}
                return [(name,value,4) for name,value in values.items()]
            def execute(self,q):self.writes.append(q)
        return Pool()
    def test_accounted_unprobed_generation_is_failed(self):
        pool=self.pool();reconcile_unprobed(pool,[26]);self.assertEqual(len(pool.writes),3)
    def test_effects_or_uncertain_tools_are_rejected(self):
        for kwargs in ({'probe':{'status':200,'requests':[{}]}},{'tool':'ExecuteExtraTool'},{'status':'started'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(RuntimeError):reconcile_unprobed(self.pool(**kwargs),[26])
