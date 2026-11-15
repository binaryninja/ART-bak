"""Only synthetic in-memory Guard entries; no user logs or tool execution."""
from contextlib import nullcontext
import unittest
from artex.guard import Guard
from artex.server.audit import AuditService,AuditBinding
from artex.server.core import Router,Request


class Tests(unittest.TestCase):
    def setUp(self):
        self.audit=Guard();self.valid=True;self.raw=True;self.select=True;self.resolved=[]
        self.binding=AuditBinding(4,'r1',self.audit)
        def resolve(selector):self.resolved.append(selector);return self.binding
        self.service=AuditService(resolve=resolve,authorize_selection=lambda *a:self.select,
            raw_authorize=lambda *a:self.raw,current=lambda b:self.valid,lifecycle_guard=lambda b:nullcontext())
        self.router=Router(lambda req:req.header('Authorization')=='yes');self.service.register(self.router)
    def call(self,target='/api/audit?task=4',auth='yes',body=b''):
        return self.router.dispatch(Request('GET',target,{'Authorization':auth},body))
    def test_exact_source_shape_chronology_omitempty(self):
        self.audit._record('Bash','allow',command='inert example')
        self.audit._record('Read','block',reason='synthetic policy')
        self.audit.post_tool_use('Bash','synthetic',False)
        response=self.call();self.assertEqual(response.status,200)
        data=response.json();self.assertEqual(data['attributions'],{'ok':1})
        self.assertEqual([e['action'] for e in data['entries']],['allow','block'])
        self.assertNotIn('reason',data['entries'][0]);self.assertNotIn('command',data['entries'][1])
    def test_auth_and_selection_before_resolver(self):
        self.assertEqual(self.call(auth='no').status,401);self.assertEqual(self.resolved,[])
        self.select=False;self.assertEqual(self.call().status,403);self.assertEqual(self.resolved,[])
    def test_missing_task_exact_source_array_active_only_explicit_resolver(self):
        self.binding=None
        self.assertEqual(self.call('/api/audit').json(),[]);self.assertEqual(self.resolved,[''])
    def test_missing_capability_not_fake_empty(self):
        router=Router(lambda r:True);AuditService().register(router)
        self.assertEqual(router.dispatch(Request('GET','/api/audit',{},b'')).status,503)
    def test_raw_permission_no_data(self):
        self.audit._record('Bash','allow',command='private synthetic')
        self.raw=False;response=self.call();self.assertEqual(response.status,403);self.assertNotIn(b'private',response.body)
    def test_no_invented_sequence_or_source_filters(self):
        for query in ('since=1','source=all','task=4&task=4','task=0','task=04','task=5'):
            with self.subTest(query=query):self.assertIn(self.call('/api/audit?'+query).status,(400,503))
    def test_bound_without_truncation(self):
        self.audit._record('Bash','allow',command='x'*65537)
        self.assertEqual(self.call().status,503)
    def test_ring_retains_only_last2000(self):
        for n in range(2001):self.audit._record('Read','allow',reason=str(n))
        entries=self.call().json()['entries'];self.assertEqual(len(entries),2000);self.assertEqual(entries[0]['reason'],'1')
    def test_callback_resource_rebind_rejected(self):
        def current(binding):self.service.guard=lambda b:nullcontext();return True
        self.service.current=current;self.service._callbacks=(self.service.resolve,self.service.authorize_selection,self.service.raw_authorize,current,self.service.guard)
        self.assertEqual(self.call().status,503)
    def test_late_revoke_hides_snapshot(self):
        original=self.audit.attributions
        def snapshot():self.valid=False;return original()
        self.audit.attributions=snapshot
        self.assertEqual(self.call().status,403)
