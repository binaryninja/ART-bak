"""Independent node-board HTTP coverage tests using inert injected adapters."""
from datetime import datetime, timezone
import unittest
from artex.db.exploration import ExplorationStore, Node
from artex.server.exploration import ExplorationService
from artex.server.core import Request


class Executor:
    def fetch_all(self, query): return [(1,)]


class Board:
    exploration_id = 10
    read_only = False
    def nodes_page(self, *args):
        return [Node(3,'fact',{},0,'confirmed','','','','',datetime.now(timezone.utc))],1
    def edges_touching(self, ids): return []
    def nodes_by_ids(self, ids): return []
    def node_assets(self, ids): return {3:[7,8]}


class NodeboardHTTPReviewTests(unittest.TestCase):
    def response(self, assets):
        store = ExplorationStore(Executor(),10)
        service = ExplorationService(lambda _:store,nodeboard_factory=lambda _:Board(),
                                     nodeboard_asset_resolver=lambda store,ids:assets)
        return service._handle('nodes',Request('GET','/api/exploration/nodes?task=1'))

    def test_missing_asset_coverage_fails_instead_of_empty_decoration(self):
        for assets in ({},{7:None},{7:{'id':7,'type':'ip','ip':'127.0.0.1'}}):
            with self.subTest(assets=assets):
                self.assertEqual(self.response(assets).status,503)

    def test_explicit_deleted_assets_are_allowed(self):
        response = self.response({7:None,8:None})
        self.assertEqual(response.status,200)
        self.assertEqual(response.json()['assets'],{})

    def test_resolver_exception_is_redacted(self):
        store = ExplorationStore(Executor(),10)
        def resolver(*args): raise RuntimeError('fixture-private-data')
        service = ExplorationService(lambda _:store,nodeboard_factory=lambda _:Board(),nodeboard_asset_resolver=resolver)
        response = service._handle('nodes',Request('GET','/api/exploration/nodes?task=1'))
        self.assertEqual(response.status,500)
        self.assertNotIn(b'fixture-private-data',response.body)
