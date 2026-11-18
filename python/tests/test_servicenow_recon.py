import json
import unittest

from artex.recon.servicenow import HOST, PAGES, RESOURCES, ServiceNowInventory, valid_input


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
        inventory, calls = self.make(b'x' * 262145)
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


if __name__ == '__main__': unittest.main()
