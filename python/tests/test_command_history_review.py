"""Independent command history source/response regressions; inert fixtures."""
import unittest
from contextlib import nullcontext
from artex.db.command_history import CommandHistoryStore,CommandScope,CommandUnavailable
from artex.server.command_history import CommandHistoryService
from artex.server.core import Request
from test_task_categories import Fake

class CommandHistoryReview(unittest.TestCase):
    def make(self,results,**kw):
        self.db=Fake(results)
        return CommandHistoryStore(self.db,CommandScope('fixed'),authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext(),raw_authorize=lambda *a:True,**kw)
    def test_stats_overflow_is_not_partial_count(self):
        store=self.make([[(False,'one',1,0),(False,'two',1,0)]],max_rows=1)
        with self.assertRaises(CommandUnavailable):store.stats()
    def test_database_error_text_not_exposed(self):
        store=self.make([])
        def fail(query):raise RuntimeError('SYNTHETIC_PRIVATE_COMMAND')
        self.db.fetch_all=fail
        service=CommandHistoryService(store,authorize=lambda *a:True)
        response=service.handle('list',Request('GET','/api/commands'))
        self.assertEqual(response.status,503)
        self.assertNotIn(b'SYNTHETIC_PRIVATE_COMMAND',response.body)
    def test_global_selector_never_narrows_by_unverified_task(self):
        store=self.make([]);service=CommandHistoryService(store,authorize=lambda *a:True)
        for query in ('task=9','task=bogus','task='):
            self.assertEqual(service.handle('list',Request('GET','/api/commands?'+query)).status,403)
        self.assertEqual(self.db.calls,[])
    def test_duplicate_detection_is_not_keyword_filtered(self):
        store=self.make([[(False,None,None,None)]])
        self.assertEqual(store.stats('only-one-input'),[])
        query=[q for _,q in self.db.calls if q][-1]
        self.assertIn('filtered u JOIN activity duplicate',query.sql)
        self.assertNotIn('JOIN filtered duplicate',query.sql)
        for key in ('exploration_id','worker','node_id','main_seg'):
            self.assertIn('duplicate.'+key,query.sql)
        self.assertIn("COALESCE(u.tool_use_id,'')<>''",query.sql)
        self.assertIn('archive_state',query.sql)
        self.assertEqual(len([q for _,q in self.db.calls if q]),1)
