import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from artex.agent.chat_compaction import ChatCompaction
from artex.agent.compaction import Budget
from artex.agent.runtime import Grant,RunResult
from artex.server.chat_setup import build_chat_application,build_conversation_runtime
from artex.db.profiles import LLMProfile
from artex.health import Registry
from artex.providers import Response
from test_chat_setup import ChatDB
from test_application import TestCodec
import test_chat_http

class CompactionSetupTests(unittest.TestCase):
    def test_resources_invalid_and_window_mismatch_before_activity(self):
        fixture=test_chat_http.ChatHTTPTests();fixture.setUp()
        for config in (object(),ChatCompaction(Budget(context_window=32000))):
            fixture.resources=replace(fixture.resources,compaction=config)
            self.assertEqual(fixture.router.dispatch(fixture.request()).status,400)
        self.assertFalse(fixture.activities.rows);self.assertFalse(fixture.queue.jobs)
    def test_resources_default_disabled(self):
        fixture=test_chat_http.ChatHTTPTests();fixture.setUp()
        self.assertIsNone(fixture.resources.compaction)
        self.assertEqual(fixture.router.dispatch(fixture.request()).status,202)
        fixture.queue.drain();self.assertEqual(len(fixture.provider.calls),1)
    def test_invalid_factory_config_before_any_factory_or_root(self):
        with tempfile.TemporaryDirectory() as parent:
            root=str(Path(parent)/'not-created')
            with patch('artex.server.runtime_setup.build_profile_runtime_application',side_effect=AssertionError('must not assemble')):
                with self.assertRaises(ValueError):
                    build_chat_application(None,None,None,transport_factory=None,health=None,transcript_root=root,
                        authorize=None,gate=None,tools=(),agent_available=None,system_prompt=None,single_process=True,compaction=object())
            with self.assertRaises(ValueError):
                build_conversation_runtime(None,None,transcript_root=root,authorize=None,gate=None,tools=(),
                    system_prompt=None,single_process=True,compaction=object())
            self.assertFalse(Path(root).exists())
    def test_actual_application_two_calls_capture_raw_and_budget(self):
        db=ChatDB();calls=[];captures=[]
        def transport(context,url,headers,body,stream):
            calls.append(json.loads(body))
            content='historical summary' if len(calls)==1 else 'current answer'
            return Response(200,json.dumps({'choices':[{'message':{'content':content},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':2,'completion_tokens':3}}))
        with tempfile.TemporaryDirectory() as root:
            bundle=build_chat_application(db,b'x'*32,TestCodec(),transport_factory=lambda config:transport,health=Registry(),
                transcript_root=root,authorize=lambda *a:True,gate=lambda *a:Grant(True,True,True),tools=(),
                agent_available=lambda key:True,system_prompt=lambda conv:'Immutable host policy',single_process=True,
                compaction=ChatCompaction(Budget(32000,128,1)),capture=captures.append)
            try:
                key=bundle.profiles.profile_coordinator.save(LLMProfile(name='fixture',model='dummy',api_key='fixture-key',streaming=False,context_window_k=32))
                bundle.profiles.profile_coordinator.set_active(key)
                tx=bundle.chat.transcript
                user={'role':'user','content':[{'type':'text','text':'old evidence '*5000}]}
                assistant={'role':'assistant','content':[{'type':'text','text':'old answer '*700}]}
                turn=tx.begin('conv-1','earlier',user)
                self.assertTrue(tx.commit('conv-1',turn,RunResult('completed',[user,assistant],{},1,0)))
                def request(path,body,token=''):
                    raw=json.dumps(body).encode();status=[]
                    env={'REQUEST_METHOD':'POST','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),
                         'wsgi.input':io.BytesIO(raw),'HTTP_IDEMPOTENCY_KEY':'new-client'}
                    if token:env['HTTP_AUTHORIZATION']='Bearer '+token
                    result=b''.join(bundle.application(env,lambda s,h:status.append(s)))
                    return int(status[0].split()[0]),json.loads(result)
                code,auth=request('/api/auth/init',{'password':'fixture-password'})
                self.assertEqual(code,200)
                self.assertEqual(request('/api/conversations/1/messages',{'message':'current question'},auth['token'])[0],202)
                drain=bundle.close(timeout=3);self.assertTrue(drain.drained)
                self.assertEqual(len(calls),2);self.assertEqual(len(captures),1)
                self.assertEqual(captures[0].request['messages'][0]['content'][0]['text'][:23],'Historical conversation')
                self.assertEqual(captures[0].request['system'],['Immutable host policy'])
                self.assertLess(captures[0].request['max_tokens'],32000)
                records=tx.store.read('conv-1')
                self.assertTrue(records)
                self.assertIn('old evidence ',json.dumps(records))
            finally:bundle.close(cancel=True,timeout=3)

if __name__=='__main__':unittest.main()
