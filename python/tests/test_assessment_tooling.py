import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from artex.agent.runtime import Action,BoundedAgent,EffectUncertain,Grant,RunStopped,Tool
from artex.assessments.definitions import CHECKS,check_allowed,capabilities,SERVICENOW
from artex.assessments.extensions import ExtensionHost
from artex.assessments.credentials import CredentialStore
from artex.recon.servicenow import ServiceNowInventory,ORIGIN
from artex.tools.http import ScopedHTTP
from test_agent_runtime import FakeProvider,assistant,request,grant

class Context(dict):
    def check(self):pass
    def remaining_seconds(self):return 30

class Tests(unittest.TestCase):
    def test_runtime_records_before_effect_and_after_result(self):
        events=[]
        def record(c,a,state,out,error):events.append((a.call_id,state,out,error))
        def run(c,a):
            self.assertEqual(events,[('call','started',None,False)])
            self.assertEqual(c['_tool_action'].call_id,'call')
            return 'observed'
        provider=FakeProvider(assistant('',[('call','inspect',{})]),assistant())
        result=BoundedAgent(provider,gate=grant,tools=[Tool('inspect','Inspect',{'type':'object'},run,lambda a:True)],tool_events=record).run(request())
        self.assertEqual(result.reason,'completed')
        self.assertEqual(events[-1],('call','completed','observed',False))

    def test_failure_and_cancelled_admission_have_honest_terminal_events(self):
        for cancelled in (False,True):
            events=[]
            from artex.manager import CancellationToken
            token=CancellationToken()
            def record(c,a,state,out,error):
                events.append((state,out))
                if cancelled and state=='started':token.cancel()
            def run(c,a):raise RuntimeError('secret exception')
            provider=FakeProvider(assistant('',[('call','inspect',{})]))
            result=BoundedAgent(provider,gate=grant,tools=[Tool('inspect','Inspect',{'type':'object'},run,lambda a:True)],tool_events=record).run(request(),token=token)
            self.assertEqual(result.reason,'cancelled' if cancelled else 'tool_error')
            self.assertEqual([x[0] for x in events],['started','stopped' if cancelled else 'failed'])
            self.assertNotIn('secret exception',str(events))

    def test_capability_registry_never_infers_admin_from_a_supplied_username(self):
        self.assertFalse(check_allowed(CHECKS['acl_definitions'],{'access_level':'unauthenticated'},True))
        self.assertFalse(check_allowed(CHECKS['integration_metadata'],{'access_level':'low_privilege'},True))
        self.assertTrue(check_allowed(CHECKS['acl_definitions'],{'access_level':'administrator'},True))
        self.assertEqual(len(SERVICENOW.operations),19)

    def test_table_pagination_preserves_evidence_and_reports_completion(self):
        paths=[];bodies=[{'result':[{'name':str(i)} for i in range(100)]},{'result':[{'name':'last'}]}]
        class Connection:
            def __init__(self,*a,**kw):pass
            def request(self,method,path,headers):paths.append(path)
            def getresponse(self):
                body=json.dumps(bodies.pop(0)).encode()
                return SimpleNamespace(status=200,read=lambda n:body[:n],getheaders=lambda:[('Content-Type','application/json')])
            def close(self):pass
        item=ServiceNowInventory('reader','private',connection_factory=Connection);item._http.interval=0
        result=json.loads(item.inspect(Context(),{'resource':'tables'}))
        self.assertEqual(result['record_count'],101);self.assertEqual(result['completeness'],'complete')
        self.assertEqual(len(result['pages']),2);self.assertIn('sysparm_offset=100',paths[1])
        self.assertTrue(all(p.startswith('/api/now/table/sys_db_object?') for p in paths))

    def test_named_script_executes_real_process_and_is_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);artifacts=root/'artifacts';artifacts.mkdir();(artifacts/'evidence.txt').write_text('actual local evidence')
            script=root/'digest.py';script.write_text('import hashlib,json,sys\nfrom pathlib import Path\nprint(json.dumps({"target":sys.argv[1],"sha256":hashlib.sha256(Path("evidence.txt").read_bytes()).hexdigest()}))\n')
            schema={'type':'object','properties':{},'additionalProperties':False}
            manifest=root/'extensions.json';manifest.write_text(json.dumps({'version':1,'tools':[{'kind':'script','name':'digest_evidence','description':'Hash local evidence','schema':schema,'read_only':True,'allowed_origins':[ORIGIN],'argv':[str(Path(sys.executable).resolve()),str(script),'{origin}']}]}))
            host=ExtensionHost(artifacts,[ORIGIN],manifest)
            runtime=SimpleNamespace(catalog=host.catalog,authorize=lambda *_:Grant(True,True,True),redact=lambda x:x)
            events=[];scope=SimpleNamespace(task_id=1,exploration_id=2,run_id=3,revision='r',session_id='session')
            tools={t.name:t for t in host.bind(runtime,scope,{'origin':ORIGIN},lambda c:c.check(),nullcontext,lambda c,a,state,out,error:events.append((a.name,state)))}
            context=Context(_tool_action=Action('tool','ExecuteExtraTool','{}','parent'))
            result=json.loads(tools['ExecuteExtraTool'].callback(context,{'name':'digest_evidence','arguments':{}}))
            self.assertEqual(result,{'target':ORIGIN,'sha256':hashlib.sha256(b'actual local evidence').hexdigest()})
            self.assertEqual(events,[('digest_evidence','started'),('digest_evidence','completed')])
            script.write_text('print("changed")')
            with self.assertRaises(PermissionError):tools['ExecuteExtraTool'].callback(context,{'name':'digest_evidence','arguments':{}})

    def test_manifest_and_artifact_discovery_do_not_expand_scope_or_keep_passwords(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);host=ExtensionHost(root/'artifacts',[ORIGIN]);text='URL: '+ORIGIN+'\nUsername: admin\nPassword: test-secret\n'
            name=host.import_text(text,redact=lambda x:x)
            saved=(host.root/name).read_text();self.assertNotIn('test-secret',saved)
            from artex.assessments.discovery import discover
            self.assertEqual(discover(saved),[{'url':ORIGIN,'username':'','password':''}])
            manifest=root/'bad.json';manifest.write_text(json.dumps({'version':1,'tools':[{'name':'outside','kind':'http','description':'outside','schema':{'type':'object','properties':{},'additionalProperties':False},'read_only':True,'allowed_origins':['https://other.service-now.com'],'path':'/'}]}))
            with self.assertRaises(ValueError):ExtensionHost(root/'other',[ORIGIN],manifest)

    def test_mcp_bridge_validates_schema_and_injects_only_the_admitted_target(self):
        remote_schema={'type':'object','properties':{'url':{'type':'string'}},'required':['url'],'additionalProperties':False}
        spec={'kind':'mcp','name':'remote_metadata','description':'Read metadata','schema':{'type':'object','properties':{},'additionalProperties':False},'read_only':True,'allowed_origins':[ORIGIN],'server':'metadata','url':'https://mcp.example.invalid/mcp','remote_tool':'inspect','remote_schema':remote_schema,'target_parameter':'url'}
        calls=[]
        class Connection:
            def __init__(self,host,port,**kw):self.frame=None;self.method=None;self.host=host
            def request(self,method,path,body,headers):
                self.method=method;self.frame=json.loads(body) if body else {};calls.append((self.host,method,path,self.frame))
            def getresponse(self):
                f=self.frame;method=f.get('method')
                result={'initialize':{'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'test','version':'1'}},'tools/list':{'tools':[{'name':'inspect','description':'Inspect','inputSchema':remote_schema}]},'tools/call':{'content':[{'type':'text','text':'metadata'}]}}.get(method,{})
                body=json.dumps({'jsonrpc':'2.0','id':f.get('id'),'result':result}).encode()
                return SimpleNamespace(status=200,read=lambda n:body[:n],getheaders=lambda:[('Content-Type','application/json'),('Mcp-Session-Id','test-session')])
            def close(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'manifest.json';p.write_text(json.dumps({'version':1,'tools':[spec]}));host=ExtensionHost(Path(tmp)/'artifacts',[ORIGIN],p)
            scope=SimpleNamespace(task_id=1,exploration_id=2,run_id=3,revision='r')
            context=Context(task_id=1,exploration_id=2,run_id=3,revision='r')
            with patch('artex.assessments.extensions.http.client.HTTPSConnection',Connection):
                self.assertEqual(host._mcp(spec,scope,context,{'origin':ORIGIN,'host':'dev366312.service-now.com'},lambda c:c.check(),nullcontext),'metadata')
            request_frame=next(frame for _,_,_,frame in calls if frame.get('method')=='tools/call')
            self.assertEqual(request_frame['params'],{'name':'inspect','arguments':{'url':ORIGIN}})
            self.assertTrue(all(hostname=='mcp.example.invalid' and path=='/mcp' for hostname,_,path,_ in calls))
            self.assertEqual(calls[-1][1],'DELETE')

if __name__=='__main__':unittest.main()
