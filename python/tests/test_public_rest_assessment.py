import hashlib,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from artex.assessments import public_rest as public

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
        self.assertNotIn('body_sample',summary['public_rest_probes']['requests'][0])
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
        runtime=SimpleNamespace(inventory=lambda _:inventory,store=SimpleNamespace(allowed_origins={'https://example.service-now.com'}),redact=lambda text:text.replace('guest-secret','[redacted]'))
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
            result=public.probes(runtime,{'origin':'https://example.service-now.com'},Context(),observations)
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
        self.assertTrue(all(r['limitations'] for r in result['requests']))
    def test_route_cannot_change_origin_or_escape_api_namespace(self):
        for route in ('https://evil.test/api/x','//evil.test/api/x','/login.do','/api/../x','/api/x?redirect=elsewhere','/api/x\\y'):
            with self.subTest(route=route),self.assertRaises(ValueError):public.concrete_path(route,'nonce')
        self.assertEqual(public.concrete_path('/api/x/{sys_id}','nonce'),'/api/x/'+'0'*32)

if __name__=='__main__':unittest.main()
