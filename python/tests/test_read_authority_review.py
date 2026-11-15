"""Post-fetch disclosure-grant regressions; synthetic query results only."""
import unittest
import test_command_history as commands
import test_token_usage as tokens
from artex.server.command_history import CommandHistoryService
from artex.server.token_usage import TokenUsageService
from artex.server.core import Request

class ReadAuthorityReview(unittest.TestCase):
    def test_command_raw_grant_revoked_after_query_hides_output(self):
        fixture=commands.CommandTests();store=fixture.make([[commands.ROW]],False)
        live=[True];original=fixture.db.fetch_all
        def fetch(query):
            rows=original(query);live[0]=False;return rows
        fixture.db.fetch_all=fetch;store.raw=lambda *a:live[0]
        service=CommandHistoryService(store,authorize=lambda *a:True)
        response=service.handle('list',Request('GET','/api/commands'))
        self.assertEqual(response.status,403);self.assertNotIn(b'inert output',response.body)
    def test_command_request_grant_revoked_after_query_hides_output(self):
        fixture=commands.CommandTests();store=fixture.make([[commands.ROW]],False)
        live=[True];original=fixture.db.fetch_all
        def fetch(query):
            rows=original(query);live[0]=False;return rows
        fixture.db.fetch_all=fetch
        response=CommandHistoryService(store,authorize=lambda *a:live[0]).handle('list',Request('GET','/api/commands'))
        self.assertEqual(response.status,403)
    def test_token_store_grant_revoked_after_query_hides_metadata(self):
        fixture=tokens.UsageTests();store=fixture.make([[(None,'private-date',1,2,3,4)]])
        live=[True];original=fixture.db.fetch_all
        def fetch(query):
            rows=original(query);live[0]=False;return rows
        fixture.db.fetch_all=fetch;store.authorize=lambda *a:live[0]
        response=TokenUsageService(store,authorize=lambda *a:True).handle('conversations',Request('GET','/api/tokens/conversations'))
        self.assertEqual(response.status,403);self.assertNotIn(b'private-date',response.body)
    def test_token_request_grant_revoked_after_query_hides_metadata(self):
        fixture=tokens.UsageTests();store=fixture.make([[(None,'private-date',1,2,3,4)]])
        live=[True];original=fixture.db.fetch_all
        def fetch(query):
            rows=original(query);live[0]=False;return rows
        fixture.db.fetch_all=fetch
        response=TokenUsageService(store,authorize=lambda *a:live[0]).handle('conversations',Request('GET','/api/tokens/conversations'))
        self.assertEqual(response.status,403)
    def test_intercept_request_grant_revoked_after_query_hides_raw_input(self):
        import test_intercept_history as intercept
        fixture=intercept.InspectionTests();fixture.make([[intercept.ROW]])
        live=[True];original=fixture.db.fetch_all
        def fetch(query):
            rows=original(query);live[0]=False;return rows
        fixture.db.fetch_all=fetch;fixture.service.authorize=lambda *a:live[0]
        response=fixture.service.handle('pending',Request('GET','/api/intercept/pending'))
        self.assertEqual(response.status,403)
        self.assertNotIn(b'"tool_input"',response.body)
    def test_all_public_aggregate_variants_revoke_request_after_fetch(self):
        cases=(('command_stats',[(False,'Synthetic',1,0)]),('token_daily',[('2026-01-01',1,2,3)]),('token_exploration',[(1,2,3,4)]))
        for name,lastrows in cases:
            with self.subTest(operation=name):
                live=[True]
                if name=='command_stats':
                    fixture=commands.CommandTests();store=fixture.make([lastrows],False)
                    service=CommandHistoryService(store,authorize=lambda *a:live[0]);op='stats';target='/api/commands/stats'
                else:
                    fixture=tokens.UsageTests()
                    results=[lastrows] if name=='token_daily' else [[(5,)],[],[],lastrows]
                    store=fixture.make(results,name=='token_exploration')
                    service=TokenUsageService(store,authorize=lambda *a:live[0]);op='daily' if name=='token_daily' else 'exploration';target='/api/tokens/daily' if op=='daily' else '/api/exploration/tokens?task=5'
                original=fixture.db.fetch_all
                def fetch(query):
                    rows=original(query);live[0]=False;return rows
                fixture.db.fetch_all=fetch
                self.assertEqual(service.handle(op,Request('GET',target)).status,403)
    def test_policy_replacement_during_query_cannot_authorize_old_snapshot(self):
        import test_intercept_history as intercept
        for surface in ('command','token','intercept'):
            for layer in ('request','store'):
                with self.subTest(surface=surface,layer=layer):
                    live=[True]
                    if surface=='command':
                        fixture=commands.CommandTests();store=fixture.make([[commands.ROW]],False)
                        service=CommandHistoryService(store,authorize=lambda *a:live[0]);op='list';target='/api/commands';field='raw'
                    elif surface=='token':
                        fixture=tokens.UsageTests();store=fixture.make([[(None,'private-date',1,2,3,4)]])
                        service=TokenUsageService(store,authorize=lambda *a:live[0]);op='conversations';target='/api/tokens/conversations';field='authorize'
                    else:
                        fixture=intercept.InspectionTests();fixture.make([[intercept.ROW]])
                        store=fixture.store;service=fixture.service;service.authorize=lambda *a:live[0];op='pending';target='/api/intercept/pending';field='raw'
                    setattr(store,field,(lambda *a:live[0]) if layer=='store' else (lambda *a:True))
                    if layer=='store':service.authorize=lambda *a:True
                    original=fixture.db.fetch_all
                    def fetch(query):
                        rows=original(query);live[0]=False
                        if layer=='request':service.authorize=lambda *a:True
                        else:setattr(store,field,lambda *a:True)
                        return rows
                    fixture.db.fetch_all=fetch
                    self.assertEqual(service.handle(op,Request('GET',target)).status,403)
