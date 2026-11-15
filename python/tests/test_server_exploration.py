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

    def board(self):
        from unittest.mock import Mock
        board=Mock(exploration_id=10,read_only=False)
        board.nodes_page.return_value=([node(1)],3)
        board.edges_touching.return_value=[Edge(1,'yields',2)]
        board.nodes_by_ids.return_value=[node(2,'fact','confirmed')]
        board.node_assets.return_value={1:[7],2:[8]}
        self.service.nodeboard_factory=Mock(return_value=board)
        self.service.nodeboard_asset_resolver=Mock(return_value={7:{'id':7,'type':'endpoint','url':'https://example.test','method':'GET'},8:None})
        return board

    def test_nodeboard_composition_and_filters(self):
        board=self.board()
        response=self.request('nodes?task=1&page=2&size=999&kind=intent,fact&state=done&order=asc&q=%2341')
        self.assertEqual(response.status,200)
        data=response.json()
        self.assertEqual((data['page'],data['size'],data['total']),(2,200,3))
        self.assertEqual(data['assets'],{'1':[{'id':'7','type':'endpoint','label':'GET https://example.test'}]})
        self.assertEqual(data['refs']['2']['type'],'fact')
        filter,page,size=board.nodes_page.call_args.args
        self.assertEqual((filter.kinds,filter.states,filter.query,filter.asc),(('intent','fact'),('done',),'#41',True))
        board.edges_touching.assert_called_once_with([1])
        board.nodes_by_ids.assert_called_once_with([2])
        board.node_assets.assert_called_once_with([1,2])
        self.service.nodeboard_asset_resolver.assert_called_once_with(self.store,[7,8])
        self.assertNotIn(('sources',),self.store.calls)

    def test_nodeboard_scope_and_capabilities_fail_closed(self):
        board=self.board();board.exploration_id=99
        self.assertEqual(self.request('nodes?task=1').status,503)
        board.nodes_page.assert_not_called()
        board.exploration_id=10;self.service.nodeboard_asset_resolver=None
        self.assertEqual(self.request('nodes?task=1').status,503)
        board.nodes_page.assert_not_called()

    def test_nodeboard_invalid_input_no_reads(self):
        board=self.board()
        for query in ('page=0','size=0','order=oops','size=-1','page=9223372036854775807&size=200','q=%00'):
            self.assertEqual(self.request('nodes?task=1&'+query).status,400)
        board.nodes_page.assert_not_called()

    def test_nodeboard_resolver_cannot_expand_assets(self):
        self.board();self.service.nodeboard_asset_resolver.return_value={99:{'id':99,'type':'ip'}}
        self.assertEqual(self.request('nodes?task=1').status,503)

    def test_nodeboard_resolver_requires_complete_explicit_coverage(self):
        self.board()
        for result in ({}, {7:None}, {7:None,8:None,9:None}):
            self.service.nodeboard_asset_resolver.return_value=result
            self.assertEqual(self.request('nodes?task=1').status,503)
        self.service.nodeboard_asset_resolver.return_value={7:None,8:None}
        response=self.request('nodes?task=1')
        self.assertEqual(response.status,200)
        self.assertEqual(response.json()['assets'],{})

    def test_nodeboard_bad_edges_or_anchor_scope_rejected(self):
        board=self.board();board.edges_touching.return_value=[Edge(77,'yields',88)]
        self.assertEqual(self.request('nodes?task=1').status,503)
        board.edges_touching.return_value=[];board.nodes_by_ids.return_value=[];board.node_assets.return_value={99:[7]}
        self.assertEqual(self.request('nodes?task=1').status,503)

    def test_nodeboard_empty_anchors_do_not_lookup_assets(self):
        board=self.board();board.node_assets.return_value={}
        response=self.request('nodes?task=1')
        self.assertEqual(response.status,200);self.assertEqual(response.json()['assets'],{})
        self.service.nodeboard_asset_resolver.assert_not_called()

    def test_nodeboard_auth_precedes_adapters(self):
        board=self.board()
        self.assertEqual(self.request('nodes?task=1',authorized=False).status,401)
        self.service.nodeboard_factory.assert_not_called()
        self.service.nodeboard_asset_resolver.assert_not_called()

if __name__=='__main__': unittest.main()
