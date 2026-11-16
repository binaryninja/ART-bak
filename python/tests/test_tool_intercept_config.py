import unittest
from contextlib import contextmanager,nullcontext
from datetime import datetime,timezone
from artex.db.tool_intercept_config import *
from artex.intercept import Interceptor
from artex.intercept.policy_owner import InterceptPolicyOwner
from artex.server.tool_intercept_mutation import ToolInterceptMutationService
from artex.server.core import Request
NOW=datetime(2026,1,1,tzinfo=timezone.utc)
class DB:
    def __init__(self,results=()):self.results=list(results);self.calls=[];self.on_query=lambda q:None;self.fail_commit=False
    def fetch_all(self,q):self.calls.append(q);self.on_query(q);return self.results.pop(0)
    def execute(self,q):self.calls.append(q);self.on_query(q);return 1
    def lock_intercept_settings(self):self.calls.append(Query("LOCK TABLE settings IN SHARE ROW EXCLUSIVE MODE"))
    @contextmanager
    def transaction(self):
        self.calls.append('begin')
        try:
            yield self
            if self.fail_commit:raise RuntimeError('private commit detail')
        except BaseException:self.calls.append('rollback');raise
        else:self.calls.append('commit')
def fixture(results=(),**kw):
    db=DB(results);s=ToolInterceptConfigStore(db);ic=Interceptor(s)
    o=InterceptPolicyOwner(s,(ic,),workspace="offline-fixture",authorize=kw.get('authorize',lambda m:True),current=kw.get('current',lambda:True),idle=kw.get('idle',lambda owners:True),lifecycle_guard=kw.get('guard',lambda:nullcontext()))
    return db,s,ic,o
class ConfigTests(unittest.TestCase):
    def test_actual_adapter_reads_literal_cache_and_invalidates(self):
        row=(4,'deny shell',True,5,'tool_name','string','Bash','deny','denied',False,0,'deny',NOW,NOW)
        db,s,ic,o=fixture([[row],[],[(8,NOW,NOW)]])
        self.assertTrue(ic.is_tool_enabled('Bash'));self.assertEqual(ic.match('Bash',{})[0].action,'deny')
        m=PolicyMutation('create',proposal=ToolRuleProposal('test',pattern='Edit'))
        r=o.mutate(m,request_authorize=lambda m:True)
        self.assertEqual(r['id'],8);self.assertIsNone(ic._rules);self.assertIsNone(ic._tools);self.assertEqual(o.generation,1)
    def test_stale_cas_no_write(self):
        db,s,ic,o=fixture([[(NOW,)]])
        with self.assertRaises(PolicyConflict):o.mutate(PolicyMutation('delete',rule_id=4,expected_updated_at=datetime(2025,1,1,tzinfo=timezone.utc)),request_authorize=lambda m:True)
        self.assertFalse(any(isinstance(q,Query) and 'DELETE' in q.sql for q in db.calls));self.assertFalse(o.unresolved)
    def test_toggle_update_delete_queries(self):
        for op in ('toggle','update','delete'):
            db,s,ic,o=fixture([[(NOW,)],[(4,NOW,NOW)] if op=='update' else [(4,)]])
            kw={'enabled':False} if op=='toggle' else {'proposal':ToolRuleProposal('x',pattern='Bash',action='allow')} if op=='update' else {}
            o.mutate(PolicyMutation(op,rule_id=4,expected_updated_at=NOW,**kw),request_authorize=lambda m:True)
            q=[q for q in db.calls if isinstance(q,Query)][-1];self.assertIn('updated_at=$2',q.sql)
    def test_tools_replacement_preserves_duplicates_empty_and_cas(self):
        db,s,ic,o=fixture([[]]);r=o.mutate(PolicyMutation('tools',tools=('Bash','Bash'),expected_tools_digest=tools_digest(None)),request_authorize=lambda m:True)
        self.assertTrue(r['ok']);self.assertIn('["Bash","Bash"]',db.calls[-2].params)
        db,s,ic,o=fixture([[('[]',)]])
        with self.assertRaises(PolicyConflict):o.mutate(PolicyMutation('tools',tools=(),expected_tools_digest=tools_digest(None)),request_authorize=lambda m:True)
    def test_unknown_commit_sticky_and_no_replay(self):
        db,s,ic,o=fixture([[(4,NOW,NOW)]]);db.fail_commit=True
        m=PolicyMutation('create',proposal=ToolRuleProposal('x',pattern='Bash'))
        with self.assertRaises(PolicyUncertain):o.mutate(m,request_authorize=lambda m:True)
        count=len(db.calls)
        with self.assertRaises(PolicyUncertain):o.mutate(m,request_authorize=lambda m:True)
        self.assertEqual(count,len(db.calls));self.assertEqual(ic.match('Bash',{})[0].action,'deny')
    def test_exact_idle_and_pending_before_sql(self):
        for mode in ('idle','pending','foreign'):
            db,s,ic,o=fixture(idle=lambda owners:mode!='idle')
            if mode=='pending':ic._pending[1]=object()
            if mode=='foreign':ic.store=object()
            with self.assertRaises(PolicyDenied):o.mutate(PolicyMutation('create',proposal=ToolRuleProposal('x',pattern='Bash')),request_authorize=lambda m:True)
            self.assertFalse(db.calls)
    def test_final_write_request_after_last_current(self):
        allowed=[True];calls=[0]
        def current():
            calls[0]+=1
            if calls[0]==2:allowed[0]=False
            return True
        db,s,ic,o=fixture(current=current)
        with self.assertRaises(PolicyDenied):o.mutate(PolicyMutation('create',proposal=ToolRuleProposal('x',pattern='Bash')),request_authorize=lambda m:allowed[0])
        self.assertFalse(any(isinstance(q,Query) and 'INSERT' in q.sql for q in db.calls))
    def test_precommit_revoke_quarantines_without_commit(self):
        allowed=[True];db,s,ic,o=fixture([[(4,NOW,NOW)]])
        db.on_query=lambda q:allowed.__setitem__(0,False) if 'INSERT' in q.sql else None
        with self.assertRaises(PolicyUncertain):o.mutate(PolicyMutation('create',proposal=ToolRuleProposal('x',pattern='Bash')),request_authorize=lambda m:allowed[0])
        self.assertNotIn('commit',db.calls);self.assertTrue(o.unresolved)
    def test_regex_and_bad_types_rejected_without_sql(self):
        for p in (ToolRuleProposal('x',pattern='.*',match_type='regex'),ToolRuleProposal('x',pattern='x',priority=True)):
            db,s,ic,o=fixture()
            with self.assertRaises(ValueError):o.mutate(PolicyMutation('create',proposal=p),request_authorize=lambda m:True)
            self.assertFalse(db.calls)
    def test_service_exact_selection_and_no_registration(self):
        db,s,ic,o=fixture([[(4,NOW,NOW)]]);seen=[]
        svc=ToolInterceptMutationService(o,authorize=lambda r,m:seen.append(m) or True)
        response=svc.handle('create',Request('POST','/api/intercept/rules',body=b'{"name":"x","pattern":"Bash"}'))
        self.assertEqual(response.status,200);self.assertTrue(all(m is seen[0] for m in seen));self.assertEqual(response.json()['match_type'],'string')
    def test_service_tools_null_empty_and_denial(self):
        db,s,ic,o=fixture([[]]);svc=ToolInterceptMutationService(o,authorize=lambda *a:True)
        response=svc.handle('tools',Request('PUT','/api/intercept/tool-config',headers={'X-Intercept-Tools-Digest':tools_digest(None)},body=b'{"enabled_tools":null}'))
        self.assertEqual(response.status,200)
        db,s,ic,o=fixture();svc=ToolInterceptMutationService(o,authorize=lambda *a:False)
        self.assertEqual(svc.handle('create',Request('POST','/x',body=b'{"name":"x","pattern":"Bash"}')).status,403);self.assertEqual(db.calls,[])
    def test_actual_guard_trace_exposes_missing_persistence_not_execution(self):
        from artex.guard import Guard
        for action in ('allow','deny','ask'):
            row=(4,'rule',True,5,'tool_name','string','Bash',action,'message',False,0,'deny',NOW,NOW)
            db,s,ic,o=fixture([[row],[]]);g=Guard(ic)
            result=g.pre_tool_use('Bash',{'command':'synthetic never executed'})
            self.assertEqual(result.decision,'' if action=='allow' else 'block')
            self.assertEqual(len(db.calls),2) # configuration SELECTs only, NO durable audit
            self.assertTrue(g.audit());self.assertFalse(ic._pending)
    def test_stored_null_malformed_and_regex_configuration_fail_closed(self):
        for raw in ('null','broken'):
            db,s,ic,o=fixture([[],[(raw,)]])
            self.assertTrue(ic.is_tool_enabled('Bash'))
        row=(4,'rule',True,5,'tool_name','regex','.*','allow','',False,0,'deny',NOW,NOW)
        db,s,ic,o=fixture([[row]])
        self.assertEqual(ic.match('Bash',{})[0].action,'deny')
    def test_custom_timestamp_timezone_rejected_without_callback(self):
        from datetime import tzinfo
        class Evil(tzinfo):
            def utcoffset(self,dt):raise AssertionError('Custom timezone invoked')
        db,s,ic,o=fixture()
        with self.assertRaises(ValueError):o.mutate(PolicyMutation('delete',rule_id=1,expected_updated_at=NOW.replace(tzinfo=Evil())),request_authorize=lambda m:True)
        self.assertFalse(db.calls)
