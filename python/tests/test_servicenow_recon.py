import json
import unittest

from artex.recon.servicenow import HOST, PAGES, RESOURCES, TABLES, MAX_PAGE_BYTES, EnumerationPolicy, ServiceNowInventory, valid_input


class Context:
    def check(self): pass
    def remaining_seconds(self): return 30


class Response:
    status = 200
    def __init__(self, body): self.body = body
    def read(self, size): return self.body[:size]
    def getheader(self, name, default=None): return {'Content-Type': 'application/json'}.get(name, default)


class InventoryTests(unittest.TestCase):
    def test_only_canonical_campaigns_get_the_runtime_marker(self):
        from artex.server.servicenow_recon import DESCRIPTION, GOAL, recon_task_view
        row = dict(id='4', description=DESCRIPTION, goal=GOAL, status='failed')
        self.assertEqual(recon_task_view(row)['runtime_kind'], 'servicenow-recon')
        self.assertNotIn('runtime_kind', row)
        self.assertNotIn('runtime_kind', recon_task_view({**row, 'goal': 'another task'}))
        self.assertIsNone(recon_task_view(None))

    def test_fixed_plan_uses_actual_provider_protocol(self):
        from artex.providers import Config, serialize
        from artex.server.servicenow_recon import ReconRuntime
        class Owner:
            states = {}
        plan = ReconRuntime.plan(Owner(), {'provisional': True})
        serialize(Config('openai', 'preflight'), plan.request)

    def make(self, body=b'{"result":[]}', status=200):
        calls = []
        class Connection:
            def __init__(self, host, port, **kwargs): calls.append(('connect', host, port))
            def request(self, method, path, headers): calls.append(('request', method, path, headers))
            def getresponse(self):
                response = Response(body)
                response.status = status
                return response
            def close(self): calls.append(('close',))
        return ServiceNowInventory('admin', 'private-password', connection_factory=Connection), calls

    def test_rejects_arbitrary_urls_paths_methods_and_extra_arguments(self):
        inventory, calls = self.make()
        for value in ({'resource': 'https://other.example/'}, {'resource': '/sys.scripts.do'},
                      {'resource': 'home', 'method': 'POST'}, {'url': '/'}, {}, None):
            with self.subTest(value=value):
                self.assertFalse(valid_input(value))
                with self.assertRaises(ValueError): inventory.inspect(Context(), value)
        self.assertEqual(calls, [])

    def test_auth_is_sent_only_to_fixed_host_and_allowed_read(self):
        inventory, calls = self.make(b'{"result":[{"user_name":"admin","password":"private-password"}]}')
        result = inventory.inspect(Context(), {'resource': 'current_user'})
        self.assertEqual(calls[0], ('connect', HOST, 443))
        self.assertEqual(calls[1][1], 'GET')
        self.assertTrue(calls[1][2].startswith('/api/now/table/sys_user?'))
        self.assertIn('Authorization', calls[1][3])
        self.assertNotIn('private-password', result)
        self.assertNotIn('password', json.loads(result)['records'][0])
        self.assertEqual(calls[-1], ('close',))

    def test_redirect_is_reported_without_following_or_forwarding_auth(self):
        inventory, calls = self.make(b'', 302)
        self.assertEqual(json.loads(inventory.inspect(Context(), {'resource': 'home'}))['status'], 302)
        self.assertEqual(len([c for c in calls if c[0] == 'connect']), 1)
        self.assertNotIn('Authorization', calls[1][3])

    def session_inventory(self, final_status=200, redirect='/login_redirect.do'):
        calls=[]
        responses=[(401,{},b'{}'),
            (200,{'Set-Cookie':'JSESSIONID=initial-secret; Path=/; Secure'},b'<form action="login.do" method="post"><input type="hidden" name="sysparm_ck" value="csrf-initial"></form>'),
            (302,{'Location':redirect,'Set-Cookie':'JSESSIONID=session-secret; Path=/; Secure'},b''),
            (200,{},b"<script>var g_ck='csrf-secret';</script>"),
            (final_status,{'Content-Type':'application/json'},b'{"result":[{"user_name":"admin"}]}'),
            (200,{'Content-Type':'application/json'},b'{"result":[]}')]
        class Connection:
            def __init__(self,host,port,**kwargs):self.host=host
            def request(self,method,path,headers,body=None):calls.append((self.host,method,path,headers,body))
            def getresponse(self):
                status,headers,body=responses.pop(0)
                result=Response(body);result.status=status;result.getheaders=lambda:list(headers.items())
                return result
            def close(self):pass
        inventory=ServiceNowInventory('admin','private-password',connection_factory=Connection)
        inventory._http.interval=0
        return inventory,calls

    def test_basic_rejection_uses_private_origin_bound_login_session(self):
        inventory,calls=self.session_inventory()
        result=json.loads(inventory.inspect(Context(),{'resource':'current_user'}))
        self.assertEqual(result['records'],[{'sys_id':'','user_name':'admin','active':''}])
        self.assertEqual(result['authentication_method'],'session')
        self.assertEqual([c[1] for c in calls],['GET','GET','POST','GET','GET'])
        self.assertTrue(all(c[0]==HOST for c in calls))
        self.assertIn(b'user_password=private-password',calls[2][4])
        self.assertIn('session-secret',calls[4][3]['Cookie'])
        self.assertEqual(calls[4][3]['X-usertoken'],'csrf-secret')
        self.assertNotIn('Authorization',calls[4][3])
        inventory.inspect(Context(),{'resource':'roles'})
        self.assertEqual(len(calls),6)
        self.assertEqual(inventory.redact('private-password session-secret csrf-secret'),'[redacted] [redacted] [redacted]')

    def test_session_identity_failure_stops_further_credential_requests(self):
        inventory, calls = self.session_inventory(final_status=401)
        inventory.inspect(Context(), {'resource': 'current_user'})
        denied = json.loads(inventory.inspect(Context(), {'resource': 'tables'}))
        self.assertEqual(denied['status'], 0)
        self.assertFalse(denied['requested'])
        self.assertEqual(len(calls),5)

    def test_login_redirect_cannot_expand_target_or_operation_scope(self):
        for redirect in ('https://other.example/login.do','/sys.scripts.do'):
            inventory,calls=self.session_inventory(redirect=redirect)
            result=json.loads(inventory.inspect(Context(),{'resource':'current_user'}))
            self.assertEqual(result['error'],'session_authentication_unavailable')
            inventory.inspect(Context(),{'resource':'tables'})
            self.assertEqual(len(calls),3)

    def test_large_body_stops_and_connection_closes(self):
        inventory, calls = self.make(b'x' * (MAX_PAGE_BYTES+1))
        self.assertEqual(json.loads(inventory.inspect(Context(), {'resource': 'tables'}))['error'], 'response_limit')
        self.assertEqual(calls[-1], ('close',))

    def test_all_paths_are_code_owned_and_bounded(self):
        inventory, _ = self.make()
        for resource in RESOURCES:
            path = inventory.path(resource)
            self.assertTrue(path.startswith('/') and not path.startswith('//'))
            if resource not in PAGES:
                self.assertIn('sysparm_limit=', path)
        with self.assertRaises(ValueError): ServiceNowInventory('admin^ORactive=true', 'password')

    def paginated(self,total,resource,*,policy=EnumerationPolicy(),visible=lambda i:True):
        from urllib.parse import parse_qs,urlsplit
        paths=[]
        class Connection:
            def __init__(self,*args,**kwargs):pass
            def request(self,method,path,headers):
                paths.append(path);q=parse_qs(urlsplit(path).query)
                self.offset=int(q.get('sysparm_offset',['0'])[0]);self.limit=int(q['sysparm_limit'][0])
            def getresponse(self):
                fields=TABLES[resource][1].split(',')
                rows=[{k:str(i) for k in fields} for i in range(self.offset,min(total,self.offset+self.limit)) if visible(i)]
                r=Response(json.dumps({'result':rows}).encode())
                r.getheaders=lambda:[('Content-Type','application/json'),('X-Total-Count',str(total))]
                return r
            def close(self):pass
        inventory=ServiceNowInventory('admin','private',connection_factory=Connection,enumeration=policy);inventory._http.interval=0
        return json.loads(inventory.inspect(Context(),{'resource':resource})),paths

    def test_large_scope_and_role_tables_are_fully_enumerated(self):
        for resource in ('application_scopes','role_inheritance'):
            with self.subTest(resource=resource):
                result,paths=self.paginated(5000,resource)
                self.assertEqual(result['record_count'],5000)
                self.assertEqual(result['completeness'],'complete')
                self.assertEqual(len(paths),20)
                self.assertEqual(result['max_records'],10000)
                self.assertTrue(all(page['body_sha256'] for page in result['pages']))

    def test_sparse_acl_pages_advance_by_requested_limit(self):
        result,paths=self.paginated(750,'data_brokers',visible=lambda i:i in (0,749))
        self.assertEqual(result['record_count'],2)
        self.assertEqual(result['completeness'],'complete')
        self.assertEqual([p['offset'] for p in result['pages']],[0,250,500])

    def test_explicit_cap_remains_honest(self):
        result,paths=self.paginated(900,'api_resources',policy=EnumerationPolicy(250,10000,600))
        self.assertEqual(result['record_count'],600)
        self.assertEqual(result['completeness'],'bounded')
        self.assertEqual(result['pagination_error'],'record_limit')
        self.assertIn('sysparm_limit=100',paths[-1])

    def test_endpoint_and_broker_inventory_has_identifiers_and_routes(self):
        inventory,_=self.make()
        for resource,table in (('api_versions','sys_ws_version'),('data_brokers','sys_ux_data_broker'),('data_broker_rest','sys_ux_data_broker_rest'),('data_broker_external_rest','sys_ux_data_broker_rest_external'),('data_broker_graphql','sys_ux_data_broker_graphql')):
            self.assertTrue(inventory.path(resource).startswith('/api/now/table/'+table+'?'))
            self.assertIn('sys_id',TABLES[resource][1].split(','))
        self.assertIn('operation_uri',TABLES['api_resources'][1].split(','))
        self.assertIn('web_service_version',TABLES['api_resources'][1].split(','))
        self.assertIn('sys_class_name',TABLES['data_brokers'][1].split(','))

    def test_rest_source_is_complete_hashed_and_credential_redacted(self):
        import hashlib
        source='// private-password\n'+'return 42;\n'*2000
        body=json.dumps({'result':[{'sys_id':'one','operation_script':source,'requires_authentication':'false','requires_acl_authorization':'true','enforce_acl':'acl-id'},{'sys_id':'two'}]}).encode()
        inventory,_=self.make(body)
        result=json.loads(inventory.inspect(Context(),{'resource':'api_scripts'}))
        first,second=result['records']
        self.assertEqual(first['operation_script'],source.replace('private-password','[redacted]'))
        self.assertEqual(first['operation_script_sha256'],hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(first['stored_script_sha256'],hashlib.sha256(first['operation_script'].encode()).hexdigest())
        self.assertEqual(first['requires_authentication'],'false')
        self.assertEqual(first['enforce_acl'],'acl-id')
        self.assertTrue(first['operation_script_available']);self.assertTrue(first['operation_script_redacted'])
        self.assertFalse(second['operation_script_available'])
        from artex.assessments.runtime import model_observations
        sample=model_observations({'api_scripts':result})['api_scripts']['records'][0]
        self.assertNotIn('operation_script',sample)
        self.assertEqual(sample['operation_script_sha256'],first['operation_script_sha256'])
        self.assertIn('operation_script',first)

    def test_script_pages_reduce_on_response_limit_without_skipping_rows(self):
        from urllib.parse import parse_qs,urlsplit
        paths=[];total=30
        class Connection:
            def __init__(self,*args,**kwargs):pass
            def request(self,method,path,headers):
                q=parse_qs(urlsplit(path).query);self.offset=int(q.get('sysparm_offset',['0'])[0]);self.limit=int(q['sysparm_limit'][0]);paths.append((self.offset,self.limit))
            def getresponse(self):
                body=b'x'*(MAX_PAGE_BYTES+1) if self.limit>12 else json.dumps({'result':[{'sys_id':str(i),'operation_script':'source '+str(i)} for i in range(self.offset,min(total,self.offset+self.limit))]}).encode()
                r=Response(body);r.getheaders=lambda:[('X-Total-Count',str(total))];return r
            def close(self):pass
        inventory=ServiceNowInventory('admin','private',connection_factory=Connection);inventory._http.interval=0
        result=json.loads(inventory.inspect(Context(),{'resource':'api_scripts'}))
        self.assertEqual([r['sys_id'] for r in result['records']],[str(i) for i in range(total)])
        self.assertEqual(result['completeness'],'complete')
        self.assertEqual(paths,[(0,25),(0,12),(12,25),(12,12),(24,25),(24,12)])
        self.assertTrue(all(page['retries'] for page in result['pages']))


if __name__ == '__main__': unittest.main()
