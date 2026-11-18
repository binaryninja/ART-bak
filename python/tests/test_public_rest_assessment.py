import hashlib,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from artex.assessments import public_rest as public
from artex.assessments import servicenow_source as source_review

class Context:
    def check(self):pass
    def remaining_seconds(self):return 30

class PublicRestTests(unittest.TestCase):
    def test_model_summary_retains_operation_index_without_response_bodies(self):
        from artex.assessments.runtime import model_observations
        rows=[dict(sys_id=str(i),name='operation',operation_uri='/api/x/'+str(i),attack_surface=['record access']) for i in range(31)]
        evidence={'public_rest_probes':{'resource':'public_rest_probes','records':rows,'requests':[{'operation_id':'0','status':200,'body_sample':'s'*60000}]},'public_rest_authorization':{'rest_acls':{'records':[{'script':'x'*2000} for _ in range(100)]}}}
        summary=model_observations(evidence)
        self.assertEqual(len(summary['public_rest_probes']['operation_index']),31)
        self.assertNotIn('body_sample',summary['public_rest_probes']['request_index'][0])
        self.assertLess(len(json.dumps(summary)),45000)
        self.assertEqual(len(evidence['public_rest_authorization']['rest_acls']['records']),100)
    def setup_case(self,changed=False):
        source='return readOnlyResult();';digest=hashlib.sha256(source.encode()).hexdigest()
        row=dict(sys_id='a'*32,name='Read record',active='true',requires_authentication='false',http_method='POST',operation_uri='/api/x/v1/items/{sys_id}',default_operation_uri='/api/x/items/{sys_id}',operation_script=source,stored_script_sha256=digest)
        observations={'api_resources':{'records':[row]},'api_scripts':{'records':[row]}}
        review=dict(route=row['operation_uri'],method='POST',body={},source_sha256=digest)
        actual=dict(row,operation_script=source+'changed' if changed else source)
        seen=[];secrets=[]
        inventory=SimpleNamespace(_remember=secrets.append)
        runtime=SimpleNamespace(_assessment_plan_key=b'fixture-key',inventory=lambda _:inventory,store=SimpleNamespace(allowed_origins={'https://example.service-now.com'}),redact=lambda text:text.replace('guest-secret','[redacted]'))
        record={'id':'assessment-fixture','origin':'https://example.service-now.com'}
        case=dict(case_id=row['sys_id']+':read',label='read',description='Read the fixture',method='POST',path_params={'sys_id':'b'*32},query={'view':'desktop'},body={},expected={'status':[200]},source_evidence={'line':1,'expression':source},safety='read_only',allow_control=False)
        plan=dict(resource='public_rest_source',assessment_id=record['id'],origin=record['origin'],records=[dict(operation_id=row['sys_id'],source_sha256=digest,helper_pins={},cases=[case],limitations=[])])
        plan['plan_signature']=source_review.signature(runtime,plan);observations['public_rest_source']=plan
        class HTTP:
            def __init__(self,origin,**options):self.allowed=options['allow_path']
            def request(self,context,method,path,headers,body=None):
                self.assert_allowed(method,path)
                if 'Authorization' in headers:raise AssertionError('Credential on anonymous request')
                seen.append((method,path,dict(headers),body))
                return dict(status=200,headers=[('Set-Cookie','JSESSIONID=guest-secret; Path=/; Secure')],body=b'guest-secret',body_sha256='hash')
            def assert_allowed(self,method,path):
                if not self.allowed(method,path):raise AssertionError('Unregistered request')
        with patch.object(public,'REVIEWED',[review]),patch.object(public,'read_rows',return_value={'complete':True,'records':[actual]}),patch.object(public,'ScopedHTTP',HTTP):
            result=public.probes(runtime,record,Context(),observations)
        return result,seen,secrets
    def test_reviewed_source_uses_isolated_cookie_less_and_guest_modes(self):
        result,seen,secrets=self.setup_case()
        self.assertEqual(result['record_count'],1)
        self.assertEqual(len(result['requests']),4)
        self.assertEqual({r['authentication'] for r in result['requests']},{'none','guest_session'})
        self.assertEqual({r['method'] for r in result['requests']},{'POST'})
        self.assertNotIn('guest-secret',json.dumps(result))
        self.assertTrue(all('Cookie' not in headers for _,_,headers,_ in seen[1::2]))
        self.assertIn('guest-secret',secrets)
    def test_changed_source_never_executes_reviewed_handler(self):
        result,seen,_=self.setup_case(changed=True)
        self.assertEqual({r['method'] for r in result['requests']},{'OPTIONS'})
        self.assertTrue(all(body is None for _,_,_,body in seen))
        self.assertTrue(result['skipped'])
    def test_input_contract_captures_path_query_body_and_metadata(self):
        text='var a=request.pathParams.id; var b=request.queryParams.sysparm_view; var c=request_body.variables; var d=requestPayload["Glide-JWT"]; getParamAsString("language");'
        result=source_review.contract(text,{'request_example':'{"csr":"example"}'})
        self.assertEqual({(x['location'],x['name']) for x in result['inputs']},{('path','id'),('query','sysparm_view'),('body','variables'),('body','Glide-JWT'),('body.params','language'),('body','csr')})
    def test_signed_plan_is_bound_to_assessment_and_cannot_be_edited(self):
        runtime=SimpleNamespace(_assessment_plan_key=b'key');record={'id':'one','origin':'https://example.service-now.com'}
        value=dict(assessment_id='one',origin=record['origin'],cases=[{'body':{}}]);value['plan_signature']=source_review.signature(runtime,value)
        self.assertTrue(source_review.verified(runtime,record,value))
        value['cases'][0]['body']['write']=True
        self.assertFalse(source_review.verified(runtime,record,value))
    def test_result_distinguishes_api_acl_from_handler_branch(self):
        case={'expected':{'status':[401],'text':'Invalid JWT'}}
        self.assertEqual(public.response_outcome(case,403,'Failed API level ACL Validation'),'api_acl_denied')
        self.assertEqual(public.response_outcome(case,401,'{"error":"Invalid JWT"}'),'expected_handler_result')
        self.assertEqual(public.response_outcome(case,200,'{}'),'unexpected_response')
    def test_route_cannot_change_origin_or_escape_api_namespace(self):
        for route in ('https://evil.test/api/x','//evil.test/api/x','/login.do','/api/../x','/api/x?redirect=elsewhere','/api/x\\y'):
            with self.subTest(route=route),self.assertRaises(ValueError):public.concrete_path(route,'nonce')
        self.assertEqual(public.concrete_path('/api/x/{sys_id}','nonce'),'/api/x/'+'0'*32)

if __name__=='__main__':unittest.main()
