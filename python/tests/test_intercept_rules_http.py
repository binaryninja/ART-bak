import json,unittest
from artex.server.intercept_rules import InterceptRuleService,proposal
from artex.server.core import Request,Router
import test_intercept_rules as fixture
ROW,NOW=fixture.ROW,fixture.NOW

class HTTPRuleTests(unittest.TestCase):
    def make(self,results,task=True):
        self.f=fixture.RuleTests();self.store=self.f.make(results,task);self.approvals=[]
        self.service=InterceptRuleService(self.store,authorize=lambda *a:True,mutation_authorize=lambda req,s,sel:self.approvals.append(sel) or True)
        return self.service
    def call(self,op,body=None,params=None,stamp=True):
        headers={'X-Rule-Updated-At':NOW.isoformat()} if stamp else {}
        return self.service.handle(op,Request('GET','/api/test',headers,b'' if body is None else json.dumps(body).encode()),params or {'id':'5','rid':'7'})
    def test_source_pattern_validation(self):
        self.assertEqual(proposal({'kind':'cidr','pattern':' 192.0.2.7/24 '},True).pattern,'192.0.2.7/24')
        for body in ({'kind':'exact_ip','pattern':'127.1'},{'kind':'exact_ip','pattern':'fe80::1%eth0'},{'kind':'cidr','pattern':'192.0.2.0/255.255.255.0'},{'kind':'exact_domain','pattern':' '},{'kind':'exact_domain','pattern':'x','action':'ALLOW'},{'kind':'exact_domain','pattern':'x','action':False}):
            with self.subTest(body=body),self.assertRaises(ValueError):proposal(body,True)
    def test_invalid_before_approval_or_sql(self):
        self.make([]);r=self.call('create',{'kind':'exact_ip','pattern':'private-invalid'})
        self.assertEqual(r.status,400);self.assertEqual(self.approvals,[]);self.assertEqual(self.f.db.calls,[]);self.assertNotIn(b'private-invalid',r.body)
    def test_create_frozen_normalized_approval(self):
        self.make([[(5,)],[(7,)],[ROW]])
        r=self.call('create',{'kind':'exact_domain','pattern':' example.test ','enabled':True})
        self.assertEqual(r.status,200);self.assertEqual(self.approvals[0].proposal.pattern,'example.test');self.assertEqual(r.json()['id'],7)
    def test_exact_task_and_expected_timestamp_required(self):
        self.make([]);self.assertEqual(self.call('list',params={'id':'6'}).status,403)
        self.assertEqual(self.call('toggle',{'enabled':False},stamp=False).status,409);self.assertEqual(self.f.db.calls,[])
    def test_list_dto_dates(self):
        self.make([[(5,)],[ROW]]);r=self.call('list');self.assertEqual(r.status,200)
        self.assertEqual(r.json()['rules'][0]['updated_at'],NOW.isoformat().replace('+00:00','Z'))
    def test_toggle_cas_and_task_deleted_bool(self):
        self.make([[(5,)],[ROW],1,[ROW]])
        self.assertEqual(self.call('toggle',{'enabled':False}).json(),{'ok':True,'enabled':False})
        self.make([[(5,)],[ROW],1]);self.assertEqual(self.call('delete').json(),{'deleted':True})
    def test_source_global_delete_id(self):
        row=list(ROW);row[6]='';self.make([[row],1],False)
        self.assertEqual(self.call('delete',params={'id':'7'}).json(),{'deleted':7})
    def test_mutation_cannot_rebind_guard(self):
        self.make([])
        def change(*a):self.store.guard=lambda *a:None;return True
        self.service.mutate=change
        self.assertEqual(self.call('create',{'kind':'exact_domain','pattern':'x'}).status,403);self.assertEqual(self.f.db.calls,[])
    def test_independent_mutation_policy_missing(self):
        self.make([]);self.service.mutate=None
        self.assertEqual(self.call('create',{'kind':'exact_domain','pattern':'x'}).status,403);self.assertEqual(self.f.db.calls,[])
    def test_routes_are_only_source_metadata_routes(self):
        self.make([]);r=Router(lambda *a:True);self.service.register(r)
        self.assertEqual(r.dispatch(Request('POST','/api/tasks/5/intercept-rules/7/execute')).status,404)
