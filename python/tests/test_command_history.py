import unittest
from contextlib import nullcontext
from artex.db.command_history import CommandHistoryStore,CommandScope,CommandDenied,CommandUnavailable
from artex.server.command_history import CommandHistoryService
from artex.server.core import Request
from test_task_categories import Fake,NOW
ROW=(1,False,8,9,'work#1','SyntheticTool','{"fixture":true}','inert output',False,NOW)
class CommandTests(unittest.TestCase):
    def make(self,results,task=True):
        self.db=Fake(results);scope=CommandScope('explicit',5,9) if task else CommandScope('explicit')
        return CommandHistoryStore(self.db,scope,authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext(),raw_authorize=lambda *a:True)
    def test_real_source_query_exact_pairing_and_bound_raw_text(self):
        s=self.make([[(5,)],[ROW]]);rows,total=s.list('fixture',0,10)
        self.assertEqual(total,1);self.assertEqual(rows[0]['output'],'inert output')
        q=[q for _,q in self.db.calls if q][-1];self.assertEqual(q.params,(5,9,'fixture','%fixture%',10,0,1048576))
        for fragment in ('r.exploration_id=u.exploration_id','r.worker IS NOT DISTINCT FROM u.worker','r.node_id IS NOT DISTINCT FROM u.node_id','r.main_seg IS NOT DISTINCT FROM u.main_seg','matches>1','duplicate.id<>u.id','octet_length(output)','CASE WHEN'):
            self.assertIn(fragment,q.sql)
        self.assertIn('count(*) FROM filtered',q.sql);self.assertNotIn('r.detail ILIKE',q.sql)
    def test_empty_page_retains_total(self):
        s=self.make([[(5,)],[(20,False,None,None,None,None,None,None,None,None)]])
        self.assertEqual(s.list(page=10),([],20))
    def test_archive_or_ambiguous_sentinel_rejected(self):
        s=self.make([[(1,True,None,None,None,None,None,None,None,None)]],False)
        with self.assertRaises(CommandUnavailable):s.list()
        q=[q for _,q in self.db.calls if q][0];self.assertIn('task_archives',q.sql);self.assertIn('FROM activity',q.sql)
    def test_raw_grant_required_separately(self):
        s=self.make([]);s.raw=None
        with self.assertRaises(CommandDenied):s.list()
        self.assertEqual(self.db.calls,[])
    def test_stats_same_filter_counts_whole_set(self):
        s=self.make([[(5,)],[(False,'SyntheticTool',12,2)]])
        self.assertEqual(s.stats('abc'),[dict(tool='SyntheticTool',total=12,errors=2)])
        q=[q for _,q in self.db.calls if q][-1];self.assertEqual(q.params[:4],(5,9,'abc','%abc%'));self.assertIn('GROUP BY 1',q.sql)
    def test_byte_sentinel_rejects_no_truncation(self):
        row=list(ROW);row[6]=None;s=self.make([[(5,)],[row]])
        with self.assertRaises(CommandUnavailable):s.list()
    def test_http_task_is_exploration_not_registry_id(self):
        s=self.make([[(5,)],[ROW]]);service=CommandHistoryService(s,authorize=lambda *a:True)
        self.assertEqual(service.handle('list',Request('GET','/api/commands?task=5')).status,403)
        response=service.handle('list',Request('GET','/api/commands?task=9'));self.assertEqual(response.status,200);self.assertEqual(response.json()['commands'][0]['exploration_id'],9)
    def test_malformed_or_missing_never_global(self):
        s=self.make([]);service=CommandHistoryService(s,authorize=lambda *a:True)
        for query in ('','?task=nope','?task=9&task=9'):
            self.assertEqual(service.handle('list',Request('GET','/api/commands'+query)).status,400)
        self.assertEqual(self.db.calls,[])
    def test_rebinding_in_grant_prevents_sql(self):
        s=self.make([])
        def changed(*a):s.executor=object();return True
        s.raw=changed
        with self.assertRaises(CommandDenied):s.list()
        self.assertEqual(self.db.calls,[])
