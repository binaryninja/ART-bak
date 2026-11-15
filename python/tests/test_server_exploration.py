"""Offline graph contracts; no live database, network, or workers."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from artex.db.exploration import Node, Edge, ExplorationStore
from artex.server.core import Router, Request
from artex.server.exploration import ExplorationService, node_dto


def node(key,kind='intent',state='done'):
    return Node(key,kind,{'text':'example'},1,state,'planner','private-worker','private-reason','',datetime(2026,11,15,tzinfo=timezone.utc))


class Store:
    def __init__(self,task=1,readonly=False):
        self.owner=task; self.exploration_id=task*10; self.read_only=readonly; self.source_task_id=task if readonly else 0
        self.items=[node(1)]; self.links=[]; self.sources=[]; self.calls=[]; self.pages={}
    def task_id(self): return self.owner
    def nodes(self,limit): self.calls.append(('nodes',limit)); return self.items
    def edges(self,limit): self.calls.append(('edges',limit)); return self.links
    def direct_sources(self): self.calls.append(('sources',)); return self.sources
    def frontier(self,limit): self.calls.append(('frontier',limit)); return self.items
    def list_by_kind(self,kind,limit): self.calls.append(('kind',kind,limit)); return self.items
    def list_by_kind_page(self,kind,before,limit):
        self.calls.append(('page',kind,before,limit)); return self.pages.get(before,(self.items,False))


class ExplorationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(); self.lookups=[]
        def resolve(key): self.lookups.append(key); return self.store if key=='1' else None
        self.router=Router(lambda request:request.header('Authorization')=='test')
        self.service=ExplorationService(resolve,active_task_id=lambda:'1')
        self.service.register(self.router)
    def request(self,suffix='graph?task=1',authorized=True,method='GET'):
        return self.router.dispatch(Request(method,'/api/exploration/'+suffix,{'Authorization':'test'} if authorized else {}))
    def source(self):
        store=Store(2,True)
        self.store.sources=[SimpleNamespace(store=store,task_id=2,exploration_id=20)]
        return store
    def test_auth_before_store(self):
        for endpoint in ('graph','frontier','intents','nodes'):
            self.assertEqual(self.request(endpoint,False).status,401)
        self.assertEqual(self.lookups,[])
    def test_graph_uses_public_dto(self):
        self.store.links=[Edge(1,'spawns',2)]
        response=self.request(); self.assertEqual(response.status,200)
        self.assertEqual(response.json()['edges'],[{'src':'1','dst':'2','rel':'spawns'}])
        dto=response.json()['nodes'][0]
        self.assertEqual(dto,{'id':'1','type':'intent','payload':'{"text":"example"}','priority':1,'state':'done','origin':'planner','ts':'2026-11-15T00:00:00Z'})
        self.assertEqual(self.store.calls[:2],[('nodes',2000),('edges',5000)])
    def test_inherited_live_work_and_edges_hidden(self):
        source=self.source(); source.items=[node(2,'fact','confirmed'),node(3,state='running'),node(4,state='done')]
        source.links=[Edge(2,'spawns',3),Edge(2,'spawns',4),Edge(4,'spawns',99)]
        response=self.request().json()
        self.assertEqual([n['id'] for n in response['nodes']],['1','2','4'])
        self.assertEqual(response['edges'],[{'src':'2','dst':'4','rel':'spawns'}])
        self.assertEqual(response['nodes'][1]['source_task_id'],'2')
        self.assertTrue(response['nodes'][1]['inherited'])
        self.assertNotIn(('sources',),source.calls)
        self.assertFalse(source.items[0].inherited)
    def test_scope_mismatch_fails(self):
        self.store.owner=2
        self.assertEqual(self.request().status,503)
        self.assertEqual(self.store.calls,[])
    def test_source_mismatch_fails(self):
        source=self.source(); source.read_only=False
        self.assertEqual(self.request().status,503)
    def test_unknown_duplicate_invalid_scope(self):
        for query in ('task=','task=0','task=-1','task=1&task=2','task=99999999999999999999','task=1&company=2'):
            self.assertEqual(self.request('graph?'+query).status,400)
        self.assertEqual(self.lookups,[])
        self.assertEqual(self.request('graph?task=2').status,404)
    def test_missing_capabilities_and_node_board(self):
        self.assertEqual(self.request('nodes?task=1').status,503)
        self.service.resolve_store=None
        self.assertEqual(self.request().status,503)
    def test_active_selection_and_empty(self):
        self.assertEqual(self.request('graph').status,200)
        self.service.active_task_id=lambda:''
        self.assertEqual(self.request('graph').json(),{'nodes':[],'edges':[]})
        self.service.active_task_id=None
        self.assertEqual(self.request('graph').status,503)
    def test_frontier_and_legacy_intents(self):
        self.assertEqual(self.request('frontier?task=1&limit=20').status,200)
        self.assertEqual(self.store.calls,[('frontier',20)])
        self.assertIsInstance(self.request('intents?task=1').json(),list)
        self.assertIn(('kind','intent',300),self.store.calls)
        self.assertEqual(self.request('frontier?task=1&limit=0').status,400)
        self.assertEqual(self.request('frontier?task=1&limit=501').status,400)
    def test_intent_history_filters_and_merges(self):
        source=self.source(); source.pages={0:([node(8,state='running'),node(7,state='done')],True),7:([node(6,state='paused'),node(5,state='stopped')],False)}
        self.store.pages={0:([node(9)],False)}
        data=self.request('intents?task=1&page=1&limit=2').json()
        self.assertEqual([n['id'] for n in data['items']],['9','7'])
        self.assertTrue(data['has_more'])
        self.assertEqual(data['items'][1]['source_task_id'],'2')
        self.assertIn(('page','intent',7,300),source.calls)
    def test_stuck_inherited_cursor_fails(self):
        source=self.source(); source.pages={0:([node(3,state='running')],True),3:([node(3,state='running')],True)}
        self.assertEqual(self.request('intents?task=1&page=1').status,503)
    def test_errors_redacted_and_no_writes(self):
        def fail(limit): raise RuntimeError('secret SQL connection')
        self.store.nodes=fail
        response=self.request()
        self.assertEqual(response.status,500); self.assertNotIn(b'secret',response.body)
        self.assertEqual(self.request(method='POST').status,405)
        self.assertEqual(self.request('nodes/1').status,404)
    def test_real_store_binding_is_checked(self):
        class Executor:
            def __init__(self): self.queries=[]
            def fetch_all(self,q):
                self.queries.append(q)
                if q.sql.startswith('SELECT id FROM tasks'): return [(1,)]
                if q.sql.startswith('SELECT id, kind'): return [(1,'intent','{}',0,'open','','','','',datetime(2026,11,15,tzinfo=timezone.utc))]
                return []
        executor=Executor(); self.service.resolve_store=lambda key:ExplorationStore(executor,10)
        response=self.request('frontier?task=1')
        self.assertEqual(response.status,200)
        self.assertEqual(executor.queries[-1].params,(10,100))
        self.assertIn('exploration_id=$1',executor.queries[-1].sql)

if __name__=='__main__': unittest.main()
