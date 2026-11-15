"""Task-template SQL/HTTP contracts with inert executors only."""
import json,unittest
from datetime import datetime,timezone
from artex.db.task_templates import TaskTemplateStore,TemplateInvalid,TemplateConflict,TemplateMissing,normalize
from artex.server.task_templates import TaskTemplateService,MAX_BODY
from artex.server.core import Router,Request
NOW=datetime(2026,11,15,tzinfo=timezone.utc)
ROW=(1,'Example','example','description','goal',None,'[]',NOW,NOW)

class Executor:
    def __init__(self):self.calls=[];self.rows=[ROW];self.count=1;self.error=None
    def fetch_all(self,q):
        self.calls.append(q)
        if self.error:raise self.error
        return self.rows
    def execute(self,q):self.calls.append(q);return self.count

class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.db=Executor();self.store=TaskTemplateStore(self.db)
        self.router=Router(lambda request:request.header('Authorization')=='test')
        TaskTemplateService(self.store).register(self.router)
    def request(self,method='GET',path='/api/task-templates',data=None,body=None,auth=True):
        return self.router.dispatch(Request(method,path,{'Authorization':'test'} if auth else {},body if body is not None else json.dumps(data).encode() if data is not None else b''))
    def test_list_auth_and_shape(self):
        self.assertEqual(self.request(auth=False).status,401);self.assertEqual(self.db.calls,[])
        data=self.request().json()['templates'][0]
        self.assertNotIn('nkey',data);self.assertEqual(data['category_id'],None);self.assertEqual(data['intercept_rules'],[])
        self.assertIn('ORDER BY updated_at DESC, id DESC',self.db.calls[-1].sql)
    def test_create_normalization_and_binding(self):
        response=self.request('POST',data={'name':'  My\tPreset  ','description':" x');-- ",'goal':' y '})
        self.assertEqual(response.status,201)
        query=self.db.calls[-1]
        self.assertEqual(query.params[:4],('My Preset','my preset',"x');--",'y'))
        self.assertNotIn("x');--",query.sql);self.assertEqual(query.params[-1],'[]')
    def test_atomic_patch_omitted_vs_clear(self):
        self.request('PATCH','/api/task-templates/1',{'category_id':None})
        query=self.db.calls[-1]
        self.assertEqual(query.params,(1,False,None,None,False,None,False,None,True,None,False,'[]'))
        self.assertIn('category_id=CASE WHEN $9',query.sql)
        self.assertEqual(len(self.db.calls),1)
    def test_rules_defaults_and_validation(self):
        data=normalize({'name':'x','description':'d','goal':'g','intercept_rules':[{'kind':'cidr','pattern':' 192.0.2.1/24 '}]})
        self.assertEqual(data['intercept_rules'],[{'enabled':False,'action':'block','kind':'cidr','pattern':'192.0.2.1/24','note':''}])
        for rule in ({'kind':'exact_ip','pattern':'hostname'},{'kind':'cidr','pattern':'192.0.2.1'},{'kind':'other','pattern':'x'},{'kind':'exact_domain','pattern':'x','action':'execute'}):
            with self.assertRaises(TemplateInvalid):normalize({'name':'x','description':'d','goal':'g','intercept_rules':[rule]})
    def test_unknown_profile_source_and_bad_fields_no_sql(self):
        for data in ({'profile_id':1},{'source_task_ids':[1]},{'name':''},{'name':None},{'name':'x'*121},{'category_id':True}):
            self.assertEqual(self.request('PATCH','/api/task-templates/1',data).status,400)
        self.assertEqual(self.db.calls,[])
    def test_duplicate_trailing_json_and_bounds(self):
        for body in (b'{"name":"a","name":"b"}',b'{} {}',b'[]',b'{"name":NaN}'):
            self.assertEqual(self.request('POST',body=body).status,400)
        self.assertEqual(self.request('POST',body=b'x'*(MAX_BODY+1)).status,413)
        self.assertEqual(self.db.calls,[])
    def test_name_conflict_and_missing(self):
        self.db.rows=[]
        self.assertEqual(self.request('POST',data={'name':'x','description':'d','goal':'g'}).status,409)
        self.assertEqual(self.request('PATCH','/api/task-templates/1',{'name':'x'}).status,404)
    def test_fk_and_unique_errors(self):
        class Error(Exception):pass
        error=Error('secret db');error.sqlstate='23503';self.db.error=error
        self.assertEqual(self.request('PATCH','/api/task-templates/1',{'category_id':7}).status,400)
        error.sqlstate='23505'
        self.assertEqual(self.request('PATCH','/api/task-templates/1',{'name':'x'}).status,409)
        error.sqlstate='XX000';response=self.request()
        self.assertEqual(response.status,500);self.assertNotIn(b'secret',response.body)
    def test_delete_only_preset(self):
        response=self.request('DELETE','/api/task-templates/7')
        self.assertEqual(response.json(),{'deleted':7})
        self.assertEqual(self.db.calls[-1].sql,'DELETE FROM task_templates WHERE id=$1')
        self.assertEqual(self.db.calls[-1].params,(7,))
        self.db.count=0;self.assertEqual(self.request('DELETE','/api/task-templates/7').status,404)
    def test_invalid_scope_and_id(self):
        for path in ('/api/task-templates?task=1','/api/task-templates/0','/api/task-templates/-1'):
            self.assertEqual(self.request('DELETE' if '?' not in path else 'GET',path).status,400)
        self.assertEqual(self.db.calls,[])
    def test_missing_store_503_no_unprotected_routes(self):
        router=Router(lambda request:True);TaskTemplateService().register(router)
        self.assertEqual(router.dispatch(Request('GET','/api/task-templates')).status,503)
        self.assertEqual(self.request('GET','/api/task-templates/1').status,405)
    def test_null_rules_clears_only_rules(self):
        self.assertEqual(self.request('PATCH','/api/task-templates/1',{'intercept_rules':None}).status,200)
        self.assertEqual(self.db.calls[-1].params[-2:],(True,'[]'))

if __name__=='__main__':unittest.main()
