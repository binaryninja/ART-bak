"""Dashboard SQL and HTTP contracts; fake runtime/executors only."""
from dataclasses import replace
import unittest
from unittest.mock import Mock
from artex.db.stats import StatsStore,StatsUnavailable
from artex.server.stats import StatsService,RuntimeStats,TaskRuntimeStats
from artex.server.core import Router,Request

TASK=TaskRuntimeStats('3','Example','Goal',True,True,False,0,100)
COUNTS={'exploration':{'intent':2,'goal':3},'in_flight':1,'goals_total':3,'goals_met':2}


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.store=Mock();self.store.asset_counts.return_value={'ip':2,'root_domain':1}
        self.store.task_counts.return_value=COUNTS.copy()
        self.runtime=Mock(return_value=RuntimeStats(True,TASK,0))
        self.router=Router(lambda r:r.header('Authorization')=='dummy')
        StatsService(self.store,runtime_snapshot=self.runtime,clock=lambda:221).register(self.router)

    def call(self,query='',auth=True):
        return self.router.dispatch(Request('GET','/api/stats'+query,{'Authorization':'dummy'} if auth else {}))

    def test_auth_precedes_adapters(self):
        self.assertEqual(self.call(auth=False).status,401)
        self.runtime.assert_not_called();self.store.asset_counts.assert_not_called()

    def test_exact_task_fields_and_stall(self):
        response=self.call('?task=3');self.assertEqual(response.status,200)
        dto=response.json()
        self.assertEqual(dto['assets'],3);self.assertEqual(dto['asset_counts'],{'ip':2,'root_domain':1})
        self.assertTrue(dto['traffic_enabled']);self.assertEqual(dto['traffic'],0)
        self.assertEqual(dto['active_task'],dict(id='3',description='Example',goal='Goal',running=True,paused=False,
            engine_mode='stalled',in_flight=1,llm_in_flight=0,last_activity=100,stalled=True,goals_total=3,goals_met=2))
        self.runtime.assert_called_once_with('3');self.store.task_counts.assert_called_once_with(3)

    def test_engine_modes_and_healthy_idle(self):
        for task,mode,running in ((replace(TASK,paused=True),'paused',False),(replace(TASK,active_calls=1),'exploring',True),
                                 (replace(TASK,started=False),'idle',False),(replace(TASK,ready=False),'idle',False),
                                 (replace(TASK,last_activity=101),'idle',True),(replace(TASK,last_activity=0),'idle',True)):
            with self.subTest(task=task):
                self.runtime.return_value=RuntimeStats(True,task)
                dto=self.call().json();self.assertEqual(dto['engine_mode'],mode)
                self.assertEqual(dto['active_task']['running'],running)
                self.assertEqual(dto['llm_configured'],task.ready)
        self.runtime.return_value=RuntimeStats(True,TASK)
        self.store.task_counts.return_value={**COUNTS,'in_flight':0}
        self.assertEqual(self.call().json()['engine_mode'],'idle')

    def test_no_active_task_only_global_and_optional_traffic(self):
        self.runtime.return_value=RuntimeStats(False)
        for query in ('','?task=','?task=active'):
            response=self.call(query)
            self.assertEqual(response.json(),{'engine_mode':'idle','llm_configured':False,'assets':3,'asset_counts':{'ip':2,'root_domain':1}})
        self.store.task_counts.assert_not_called()
        self.assertEqual(self.call('?task=3').status,404)

    def test_explicit_scope_never_follows_other_active_task(self):
        self.assertEqual(self.call('?task=4').status,503)
        self.store.asset_counts.assert_not_called()
        self.assertEqual(self.call('?task=0003').status,200)
        self.runtime.assert_called_with('3')

    def test_invalid_duplicate_queries_before_adapters(self):
        for query in ('?task=0','?task=-1','?task=abc','?task=9223372036854775808','?task=3&task=4','?task_id=3','?scope=all'):
            self.assertEqual(self.call(query).status,400)
        self.runtime.assert_not_called()

    def test_missing_and_invalid_runtime_not_zero(self):
        router=Router(lambda _:True);StatsService(self.store).register(router)
        self.assertEqual(router.dispatch(Request('GET','/api/stats')).status,503)
        for value in (None,{},RuntimeStats(True,replace(TASK,ready=1)),RuntimeStats(True,replace(TASK,id='private')),RuntimeStats(True,traffic_count=-1)):
            self.runtime.return_value=value
            self.assertEqual(self.call().status,503)
        self.store.asset_counts.assert_not_called()

    def test_read_errors_redacted_not_zero(self):
        self.store.asset_counts.side_effect=RuntimeError('secret SQL')
        response=self.call();self.assertEqual(response.status,500)
        self.assertNotIn(b'secret',response.body)
        self.assertNotIn('assets',response.json())

    def test_missing_persisted_task_404(self):
        self.store.task_counts.return_value=None
        self.assertEqual(self.call('?task=3').status,404)

    def test_real_store_exact_bound_aggregates_caps_and_mapping(self):
        executor=Mock()
        executor.fetch_all.side_effect=[[('ip',2)],[(9,)],[('intent',5),('goal',3)]]
        executor.fetch_one.side_effect=[(2,),(3,1)]
        store=StatsStore(executor,allow_global=True)
        self.assertEqual(store.asset_counts(),{'ip':2})
        self.assertEqual(store.task_counts(3),{'exploration':{'intent':5,'goal':3},'in_flight':2,'goals_total':3,'goals_met':1})
        queries=[call.args[0] for call in executor.fetch_all.call_args_list]
        self.assertEqual(queries[0].sql,'SELECT type, COUNT(*) FROM assets GROUP BY type')
        self.assertEqual(queries[1].params,(3,));self.assertIn('deleted_at IS NULL',queries[1].sql);self.assertIn('archived_at IS NULL',queries[1].sql)
        self.assertEqual(queries[2].params,(9,))
        limited=[call.args[0] for call in executor.fetch_one.call_args_list]
        self.assertEqual(limited[0].params,(9,'intent',100000))
        self.assertEqual(limited[1].params,(9,'goal',10000))
        for query in limited:self.assertIn('ORDER BY id DESC LIMIT $3) selected',query.sql)
        executor.execute.assert_not_called()

    def test_store_global_opt_in_empty_and_invalid_aggregates(self):
        executor=Mock()
        with self.assertRaises(StatsUnavailable):StatsStore(executor).asset_counts()
        executor.fetch_all.assert_not_called()
        executor.fetch_all.return_value=[]
        store=StatsStore(executor,allow_global=True)
        self.assertEqual(store.asset_counts(),{})
        self.assertIsNone(store.task_counts(3))
        executor.fetch_one.assert_not_called()
        for rows in ([('ip',-1)],[('ip',True)],[('ip',1),('ip',2)]):
            executor.fetch_all.return_value=rows
            with self.assertRaises(StatsUnavailable):store.asset_counts()

    def test_archived_mapping_rejected_before_pruned_aggregates(self):
        class ArchivedExecutor:
            def __init__(self):self.queries=[]
            def fetch_all(self,query):
                self.queries.append(query)
                if 'FROM assets' in query.sql:return [('ip',2)]
                if 'SELECT exploration_id FROM tasks' in query.sql:
                    # Model an archived_at != NULL, deleted_at == NULL row:
                    # deleted-only SQL would incorrectly admit its pruned graph.
                    return [] if 'archived_at IS NULL' in query.sql else [(9,)]
                raise AssertionError('Archived task must not query exploration aggregates')
            def fetch_one(self,query):raise AssertionError('No counters for archived task')
        executor=ArchivedExecutor()
        router=Router(lambda _:True)
        StatsService(StatsStore(executor,allow_global=True),runtime_snapshot=lambda _:RuntimeStats(True,TASK)).register(router)
        response=router.dispatch(Request('GET','/api/stats?task=3'))
        self.assertEqual(response.status,404)
        self.assertNotIn('exploration',response.json())
        self.assertEqual(len(executor.queries),2)
        self.assertEqual(executor.queries[-1].params,(3,))

    def test_real_store_and_router_end_to_end_empty_dashboard(self):
        executor=Mock();executor.fetch_all.return_value=[]
        router=Router(lambda _:True)
        StatsService(StatsStore(executor,allow_global=True),runtime_snapshot=lambda selector:RuntimeStats(False)).register(router)
        self.assertEqual(router.dispatch(Request('GET','/api/stats')).json(),{'engine_mode':'idle','llm_configured':False,'assets':0,'asset_counts':{}})
        self.assertEqual(executor.fetch_all.call_count,1)


if __name__=='__main__':unittest.main()
