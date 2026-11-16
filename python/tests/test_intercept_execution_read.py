import unittest,json
from contextlib import contextmanager
from datetime import datetime,timezone
from artex.db.intercept_detail_read import InterceptDetailReader
from artex.db.intercept_history import InspectionScope
from artex.db.intercept_execution_read import InterceptExecutionReader
from artex.server.intercept_execution_read import InterceptExecutionReadService
from artex.server.core import Request

class Tests(unittest.TestCase):
    def setUp(self):
        self.queries=[];self.parents=[];self.allowed=True;self.raw=True;self.parent_allowed=True;self.guard_exit=None
        self.now=datetime(2026,1,1,tzinfo=timezone.utc);self.task_live=True;self.hint_exists=True
        self.detail=(True,7,None,None,'5','worker','Bash','{}','allowed','reason',None,self.now,'model','','','',json.dumps({'correlation':'exact','tool_use_id':'call'}))
        self.activity=[(True,10,8,'worker','tool_use','Bash','call',False,'synthetic',self.now,None),(True,11,8,'worker','tool_result','','call',False,'result',self.now,0)]
        class DB:
            def fetch_all(db,q):
                self.queries.append(q)
                if 'FROM intercept_pending ip' in q.sql:return [] if self.detail is None else [self.detail]
                if q.sql.startswith('SELECT exploration_id'):return [(9,)] if self.task_live else []
                if q.sql.startswith('SELECT EXISTS'):return [(self.hint_exists,)]
                return self.activity
        @contextmanager
        def guard(scope):
            yield
            if self.guard_exit:self.guard_exit()
        self.reader=InterceptExecutionReader(InterceptDetailReader(DB(),InspectionScope('workspace')))
        def parent(req,scope,id,value):self.parents.append(value);return self.parent_allowed
        self.service=InterceptExecutionReadService(self.reader,authorize=lambda *a:self.allowed,raw_authorize=lambda *a:self.raw,parent_authorize=parent,current=lambda *a:True,lifecycle_guard=guard)
        self.request=Request('GET','/api/intercept/history/7/execution')
    def test_task_pair_exact_original_scope_and_shape(self):
        result=self.service.handle(self.request);self.assertEqual(result.status,200)
        self.assertEqual(result.json()['session'],'intent:8');self.assertEqual(result.json()['seq'],10)
        self.assertEqual(result.json()['task_id'],'5');self.assertIsNone(result.json()['conversation_id'])
        self.assertNotIn('metadata',result.json()['items'][0]);self.assertNotIn('detail',result.json()['items'][0])
        queries=[q for q in self.queries if 'FROM activity WHERE' in q.sql]
        self.assertTrue(queries);self.assertTrue(all(q.params[:2]==(9,'call') and 'LIMIT 3' in q.sql for q in queries))
    def test_conversation_precedence_no_task_lookup(self):
        self.detail=(*self.detail[:3],12,*self.detail[4:])
        self.activity=[(True,10,None,'worker','tool_use','Bash','call',False,'synthetic',self.now,None)]
        result=self.service.handle(self.request);self.assertEqual(result.status,200);self.assertEqual(result.json()['session'],'')
        self.assertFalse(any(q.sql.startswith('SELECT exploration_id') for q in self.queries))
    def test_missing_ambiguous_duplicate_and_deleted(self):
        self.activity=[];self.assertEqual(self.service.handle(self.request).status,410)
        self.task_live=False;self.assertEqual(self.service.handle(self.request).status,410)
        self.task_live=True;self.activity=[(True,10,8,'w','tool_use','Bash','call',False,'',self.now,None)]*3
        self.assertEqual(self.service.handle(self.request).status,409)
        self.detail=(*self.detail[:-1],None);self.assertEqual(self.service.handle(self.request).status,409)
        self.detail=None;self.assertEqual(self.service.handle(self.request).status,404)
    def test_hint_only_missing_requires_separate_existence_grant(self):
        self.detail=None;self.hint_exists=False;req=Request('GET',self.request.target+'?conversation=12')
        self.parent_allowed=False;self.assertEqual(self.service.handle(req).status,403)
        self.assertFalse(any(q.sql.startswith('SELECT EXISTS') for q in self.queries))
        self.parent_allowed=True;self.assertEqual(self.service.handle(req).status,410)
        self.assertTrue(all(p.kind=='conversation_hint' for p in self.parents))
    def test_parent_grant_denial_never_reads_activity(self):
        self.parent_allowed=False;self.assertEqual(self.service.handle(self.request).status,403)
        self.assertFalse(any('FROM activity WHERE' in q.sql for q in self.queries))
    def test_guard_exit_delete_withholds_stale_navigation(self):
        self.guard_exit=lambda:setattr(self,'task_live',False)
        result=self.service.handle(self.request);self.assertEqual(result.status,403);self.assertNotIn(b'synthetic',result.body)
    def test_bad_pairing_refused_and_single_call_valid(self):
        self.activity[1]=(True,11,99,'worker','tool_result','','call',False,'result',self.now,0)
        self.assertEqual(self.service.handle(self.request).status,409)
        self.activity=self.activity[:1];self.assertEqual(self.service.handle(self.request).status,200)
    def test_byte_sentinel_and_raw_denial(self):
        self.raw=False;self.assertEqual(self.service.handle(self.request).status,403);self.assertFalse(self.queries)
        self.raw=True;self.activity=[(False,*self.activity[0][1:])]
        self.assertEqual(self.service.handle(self.request).status,500)
