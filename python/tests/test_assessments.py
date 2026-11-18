import json
from pathlib import Path
import tempfile
import unittest
from artex.assessments.credentials import CredentialStore
from artex.assessments.definitions import capabilities,instance_origin,SERVICENOW
from artex.assessments.discovery import discover,source_metadata
from artex.assessments.store import AssessmentStore
from artex.agent.domain_tools import DomainTools
from artex.agent.runtime import Tool
from artex.assessments.runtime import catalog

class AssessmentTests(unittest.TestCase):
    def test_credentials_are_origin_bound_private_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            store=CredentialStore(Path(directory)/'credentials',b'k'*32)
            ref=store.put('https://a.service-now.com','reader','secret')
            self.assertEqual(ref,store.put('https://a.service-now.com','reader','secret'))
            self.assertEqual(store.get(ref,'https://a.service-now.com'),('reader','secret'))
            self.assertEqual((store.root/ref).stat().st_mode & 0o777,0o600)
            with self.assertRaises(ValueError):store.get(ref,'https://b.service-now.com')
            self.assertNotEqual(ref,store.put('https://a.service-now.com','reader','new-secret'))

    def test_discovery_associates_credentials_without_persisting_the_text(self):
        text='URL: https://dev123.service-now.com/login.do\nUsername: reader\nPassword: example-secret\n'
        result=discover(text)
        self.assertEqual(result,[dict(url='https://dev123.service-now.com',username='reader',password='example-secret')])
        self.assertNotIn('example-secret',json.dumps(source_metadata(text,'evidence.txt')))
        with self.assertRaises(ValueError):discover(text+'https://dev456.service-now.com/\n')
        second='URL: https://dev456.service-now.com/\nUsername: other\nPassword: another-secret\n'
        self.assertEqual([x['username'] for x in discover(text+'\n'+second)],['reader','other'])

    def test_scope_remains_an_exact_host_authority(self):
        store=AssessmentStore(None,['https://dev123.service-now.com'])
        self.assertEqual(store.approved('https://dev123.service-now.com/login.do'),'https://dev123.service-now.com')
        for value in ('https://dev456.service-now.com','https://dev123.service-now.com.evil.test','http://dev123.service-now.com','https://user:password@dev123.service-now.com','https://dev123.service-now.com:444'):
            with self.subTest(value=value),self.assertRaises((ValueError,PermissionError)):store.approved(value)

    def test_privilege_is_derived_from_observed_identity_and_roles(self):
        rows={'current_user':{'status':200,'records':[{'user_name':'reader'}]},'roles':{'status':200,'records':[{'role.name':'admin'}]},'tables':{'status':403}}
        self.assertEqual(capabilities(rows,'reader')['access_level'],'administrator')
        self.assertEqual(capabilities(rows,'other')['access_level'],'unauthenticated')
        self.assertIn('tables',capabilities(rows,'reader')['limitations'])
        self.assertEqual(capabilities({'current_user':rows['current_user']},'reader')['access_level'],'authenticated_unknown')

    def test_domain_tool_binding_requires_exact_catalog(self):
        schema={'type':'object','properties':{}}
        row=catalog('inventory','Inventory',schema,['worker'])
        tool=Tool('inventory','Inventory',schema,lambda c,a:'ok',lambda a:True)
        domain=DomainTools('v1',(row,),lambda *a:(tool,))
        self.assertEqual(domain.tools(None,None,None),(tool,))
        with self.assertRaises(ValueError):DomainTools('v1',(row,),lambda *a:()).tools(None,None,None)

    def test_existing_read_tool_can_trigger_discovery_without_returning_password(self):
        from types import SimpleNamespace
        from artex.assessments.discovery import DiscoveryDispatcher
        from artex.tools.filesystem import ScopedFilesystem
        from artex.agent.runtime import RuntimeContext
        from artex.manager import CancellationToken
        import time
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'evidence.txt').write_text('https://dev123.service-now.com/\nusername: reader\npassword: private-example\n')
            calls=[]
            runtime=SimpleNamespace(store=AssessmentStore(None,['https://dev123.service-now.com']),credentials=CredentialStore(root/'credentials',b'k'*32),submit=lambda definition,origin,ref,source:calls.append((definition,origin,ref,source)) or {'task_id':42,'id':'assessment'})
            observer=DiscoveryDispatcher(runtime).read_observer(authorize=lambda context,path:context.get('task_id')==7 and path=='evidence.txt')
            context=RuntimeContext({'task_id':7},CancellationToken(),time.monotonic()+30,time.monotonic)
            with ScopedFilesystem(str(root),read_observer=observer) as filesystem:
                tool=next(t for t in filesystem.tools() if t.name=='Read')
                result=tool.callback(context,{'file_path':'evidence.txt'})
            self.assertNotIn('private-example',result)
            self.assertEqual(calls[0][1],'https://dev123.service-now.com')
            self.assertNotIn('private-example',json.dumps(calls))
            self.assertEqual(calls[0][3]['task_id'],'7')

    def test_shared_assembly_accepts_explicit_domain_catalog(self):
        import test_task_runtime as fixture
        f=fixture.Tests();f.setUp()
        try:
            from artex.agent.task_runtime import TaskRuntimeAssembly
            from artex.agent.engine import EngineScope
            from artex.manager import CancellationToken
            from contextlib import nullcontext
            a=f.assembly
            schema={'type':'object','properties':{}}
            domain=DomainTools('inventory-v1',(catalog('inventory','Inventory',schema,['worker']),),lambda *args:(Tool('inventory','Inventory',schema,lambda c,a:'ok',lambda a:True),))
            runtime=TaskRuntimeAssembly(f.p.db,providers=f.p.resolver,transcript=f.transcript,prompts=a._prompts,policy_revision='test',current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext(),authorize=a._authorize,asset_authorize=a._assets,validate_input=a._input,graph_catalog=a._catalog,domain_tools=domain)
            conf=runtime.bootstrap_configuration(f.p.snapshot)
            handle=runtime.engine_factory(f.p.snapshot,CancellationToken(),EngineScope(5,9,71,conf.policy_revision),lambda *a:True,lambda *a:nullcontext())
            self.assertIsNotNone(handle.worker)
            self.assertIn('inventory-v1',runtime._configuration_bytes)
            self.assertEqual(f.p.calls,[])
        finally:f.doCleanups()

    def test_provider_secret_resolver_does_not_modify_stored_profile(self):
        from copy import deepcopy
        import test_task_providers as fixture
        from artex.providers.factory import ProfileFactory
        f=fixture.Tests();f.setUp()
        try:
            profile=deepcopy(f.rows[2]);profile.api_key=''
            factory=ProfileFactory(lambda config:lambda context,request:None,credential_resolver=lambda identifier:'host-secret' if identifier==2 else '')
            self.assertEqual(factory.validate(profile).api_key,'host-secret')
            self.assertEqual(profile.api_key,'')
            factory.build(profile)
            self.assertEqual(profile.api_key,'')
        finally:f.doCleanups()
