"""Goal CRUD through actual scoped graph stores, only inert transactional SQL."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timezone
import json,unittest
from artex.server.task_goals import TaskGoalService,GoalBinding
from artex.server.core import Request,Router
from artex.db.exploration import ExplorationStore,GraphNotFound,GraphInvalid

NOW=datetime(2026,1,1,tzinfo=timezone.utc)
def node(id,kind,payload,state='open',origin='human'):
    return (id,kind,json.dumps(payload),0,state,origin,'','','',NOW)

class DB:
    def __init__(self):
        self.rows={1:node(1,'fact',{'summary':'root'},'origin','system'),2:node(2,'goal',{'text':'old','vulnclass':'x'},'met')}
        self.edges={(1,'spawns',2),(1,'proves',2)};self.calls=[];self.events=[]
        self.parent=(5,'created',False,False);self.fail_link=False;self.hook=None;self.count_override=None
    @contextmanager
    def transaction(self):
        old=deepcopy((self.rows,self.edges));self.events.append('begin')
        try:yield self
        except BaseException:self.rows,self.edges=old;self.events.append('rollback');raise
        else:self.events.append('commit')
    def fetch_all(self,q):
        self.calls.append(q)
        if q.sql.startswith('SELECT id,status'):return [self.parent] if self.parent else []
        if '/* bounded_goal */' in q.sql:
            id,exp,payload_cap,origin_cap=q.params;assert exp==9
            if id not in self.rows or self.rows[id][1]!='goal':return []
            row=list(self.rows[id])
            if len(row[2].encode())>payload_cap:row[2]=None
            if len(row[4].encode())>32:row[4]=None
            if len(row[5].encode())>origin_cap:row[5]=None
            return [tuple(row)]
        if 'AS bounded_goals' in q.sql:
            assert q.params[0]==9
            goals=[r for _,r in sorted(self.rows.items(),reverse=True) if r[1]=='goal']
            return [(*r,len(goals)) for r in goals]
        if q.sql.startswith('SELECT id FROM exploration_nodes'):
            if "kind='fact'" in q.sql:return [(i,) for i,r in self.rows.items() if r[1]=='fact' and r[4]=='origin'][:1]
            id,exp=q.params;assert exp==9
            return [(id,)] if id in self.rows and self.rows[id][1]=='goal' else []
        if 'WHERE id=$1 AND exploration_id=$2' in q.sql:
            id,exp=q.params;assert exp==9;return [self.rows[id]] if id in self.rows else []
        if 'AND kind=$2' in q.sql:
            exp,kind,limit=q.params;assert exp==9
            return [r for _,r in sorted(self.rows.items(),reverse=True) if r[1]==kind][:limit]
        raise AssertionError(q.sql)
    def fetch_one(self,q):
        self.calls.append(q);exp,kind,payload,priority,state,origin=q.params;assert exp==9
        id=max(self.rows)+1;self.rows[id]=(id,kind,payload,priority,state,origin,'','','',NOW)
        return (id,)
    def execute(self,q):
        self.calls.append(q)
        if self.count_override is not None:return self.count_override
        if q.sql.startswith('INSERT INTO exploration_edges'):
            if self.fail_link:return 0
            exp,src,rel,dst=q.params;assert exp==9;self.edges.add((src,rel,dst))
        elif q.sql.startswith('UPDATE exploration_nodes SET payload'):
            payload,id,exp=q.params;assert exp==9
            if id not in self.rows or self.rows[id][1]!='goal':return 0
            r=list(self.rows[id]);r[2]=payload;self.rows[id]=tuple(r)
        elif q.sql.startswith('DELETE FROM exploration_nodes'):
            id,exp=q.params;assert exp==9 and "kind='goal'" in q.sql
            if id not in self.rows or self.rows[id][1]!='goal':return 0
            del self.rows[id];self.edges={e for e in self.edges if id not in (e[0],e[2])}
        else:raise AssertionError(q.sql)
        if self.hook:self.hook()
        return 1

class GoalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.graph=ExplorationStore(self.db,9);self.binding=GoalBinding(5,9,'rev',self.graph)
        self.valid=True;self.idle=True;self.allow=True;self.delete=True;self.policy_calls=[];self.resolve_calls=[]
        self.router=self.make()
    def make(self,**kw):
        def resolve(req,id):self.resolve_calls.append(id);return self.binding
        @contextmanager
        def guard(binding):yield
        def policy(req,action,binding,proposal):self.policy_calls.append(proposal);return self.allow
        opts=dict(resolve=resolve,authorize=policy,current=lambda b:self.valid,lifecycle_guard=guard,
                  mutation_authorize=policy,delete_authorize=lambda *a:self.delete,idle=lambda b:self.idle)
        opts.update(kw);self.service=TaskGoalService(self.db,**opts)
        router=Router(lambda req:req.header('Authorization')=='fixture');self.service.register(router);return router
    def call(self,method='GET',body=None,gid=None,auth=True,query='',raw=None):
        path='/api/tasks/5/goals'+('' if gid is None else '/'+str(gid))+query
        data=raw if raw is not None else b'' if body is None else json.dumps(body).encode()
        return self.router.dispatch(Request(method,path,{'Authorization':'fixture' if auth else ''},data))
    def test_list_source_dto(self):
        response=self.call();self.assertEqual(response.status,200)
        self.assertEqual(response.json(),{'goals':[{'id':'2','text':'old','vulnclass':'x','state':'met','origin':'human','ts':'2026-01-01T00:00:00Z'}]})
    def test_create_atomic_root_lineage_and_human_origin(self):
        response=self.call('POST',{'text':' new ','vulnclass':' SQLi '})
        self.assertEqual(response.status,200);self.assertEqual(response.json()['state'],'open');self.assertEqual(response.json()['origin'],'human')
        self.assertEqual(response.json()['text'],'new');self.assertIn((1,'spawns',3),self.db.edges)
        self.assertFalse(any('UPDATE tasks' in q.sql for q in self.db.calls))
    def test_patch_preserves_state_and_evidence_edges(self):
        edges=deepcopy(self.db.edges)
        response=self.call('PATCH',{'text':' changed '},gid=2)
        self.assertEqual(response.status,200);self.assertEqual(response.json()['state'],'met')
        self.assertNotIn('vulnclass',response.json());self.assertEqual(self.db.edges,edges)
    def test_delete_only_goal_and_cascade_edges_not_fact(self):
        self.assertEqual(self.call('DELETE',gid=2).json(),{'ok':True})
        self.assertIn(1,self.db.rows);self.assertNotIn(2,self.db.rows);self.assertEqual(self.db.edges,set())
    def test_wrong_kind_never_updated_or_deleted(self):
        for method,body in (('PATCH',{'text':'bad'}),('DELETE',None)):
            self.assertEqual(self.call(method,body,gid=1).status,404)
        self.assertEqual(self.db.rows[1][1],'fact')
        self.assertFalse(any(q.sql.startswith(('UPDATE','DELETE')) for q in self.db.calls))
    def test_missing_or_failed_lineage_rolls_back_created_goal(self):
        self.db.fail_link=True
        self.assertEqual(self.call('POST',{'text':'new'}).status,503);self.assertEqual(set(self.db.rows),{1,2})
        self.db.fail_link=False;del self.db.rows[1]
        self.assertEqual(self.call('POST',{'text':'new'}).status,503);self.assertEqual(set(self.db.rows),{2})
    def test_active_undrained_or_no_policy_fails_closed(self):
        for parent in ((5,'running',False,False),(5,'done',True,False),(5,'created',False,True)):
            self.db.parent=parent;self.assertEqual(self.call('POST',{'text':'x'}).status,409)
        self.db.parent=(5,'created',False,False);self.idle=False
        self.assertEqual(self.call('DELETE',gid=2).status,409)
        self.router=self.make(delete_authorize=None);self.assertEqual(self.call('DELETE',gid=2).status,503)
        self.router=self.make(mutation_authorize=None);self.assertEqual(self.call('POST',{'text':'x'}).status,503)
        self.assertEqual(set(self.db.rows),{1,2})
    def test_auth_scope_executor_and_inherited_rejected(self):
        self.assertEqual(self.call(auth=False).status,401);self.assertEqual(self.resolve_calls,[])
        self.binding=GoalBinding(6,9,'rev',self.graph);self.assertEqual(self.call().status,403)
        self.binding=GoalBinding(5,9,'rev',ExplorationStore(self.db,9,read_only=True));self.assertEqual(self.call().status,503)
        self.binding=GoalBinding(5,9,'rev',ExplorationStore(DB(),9));self.assertEqual(self.call().status,503)
        self.assertEqual(self.db.calls,[])
    def test_state_injection_duplicates_and_invalid_ids(self):
        for body in ({'text':'x','state':'met'},{'text':' '},{'text':'x','vulnclass':None},{'text':'x','task_id':6}):
            self.assertEqual(self.call('PATCH',body,gid=2).status,400)
        self.assertEqual(self.call('POST',raw=b'{"text":"x","text":"y"}').status,400)
        self.assertEqual(self.call('DELETE',gid=0).status,400);self.assertEqual(self.call(query='?task=6').status,400)
        self.assertEqual(self.db.calls,[])
    def test_stale_after_write_rolls_back_and_delete_denial(self):
        self.delete=False;self.assertEqual(self.call('DELETE',gid=2).status,403)
        self.db.hook=lambda:setattr(self,'valid',False)
        self.assertEqual(self.call('PATCH',{'text':'changed'},gid=2).status,409)
        self.assertEqual(json.loads(self.db.rows[2][2])['text'],'old');self.assertEqual(self.db.events[-1],'rollback')
    def test_coordinated_executor_rebinding_blocked(self):
        foreign=DB()
        def policy(*a):self.service.executor=foreign;self.graph.executor=foreign;return True
        self.router=self.make(mutation_authorize=policy)
        self.assertEqual(self.call('POST',{'text':'x'}).status,503)
        self.assertEqual(self.db.calls,[]);self.assertEqual(foreign.calls,[])
    def test_delete_store_guard_and_rowcount(self):
        with self.assertRaises(GraphNotFound):self.graph.delete_goal(1)
        with self.assertRaises(PermissionError):ExplorationStore(self.db,9,read_only=True).delete_goal(2)
        for count in (-1,True,2):
            self.db.count_override=count
            with self.assertRaises(GraphInvalid):self.graph.delete_goal(2)
        with self.assertRaises(NotImplementedError):self.graph.delete_node(2)

    def test_oversized_legacy_payload_never_fetched_by_mutations(self):
        from unittest.mock import patch
        self.db.rows[1]=node(1,'fact',{'summary':'x'*100000},'origin','system')
        self.db.rows[2]=node(2,'goal',{'text':'x'*100000},'met')
        with patch.object(ExplorationStore,'get_node',side_effect=AssertionError('Unbounded read')):
            self.assertEqual(self.call('PATCH',{'text':'safe'},gid=2).status,200)
            self.assertEqual(self.call('POST',{'text':'new'}).status,200)
            self.assertEqual(self.call('DELETE',gid=2).status,200)
        self.assertIn((1,'spawns',3),self.db.edges)
    def test_oversized_legacy_origin_response_rolls_back_patch(self):
        self.db.rows[2]=node(2,'goal',{'text':'old'},'met','x'*129)
        self.assertEqual(self.call('PATCH',{'text':'new'},gid=2).status,503)
        self.assertEqual(json.loads(self.db.rows[2][2])['text'],'old')
        self.assertEqual(self.db.events[-1],'rollback')
