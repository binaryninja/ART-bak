"""Real assembly, temp JSONL, fake SQL and transport; no real model/network."""
import io
import json
import tempfile
import threading
import unittest
from datetime import datetime,timezone
from artex.server.chat_setup import build_conversation_runtime, build_chat_application
from artex.server.runtime_setup import build_profile_runtime_application
from artex.agent.runtime import Grant
from artex.health import Registry
from artex.providers import Response
from artex.db.profiles import LLMProfile
from test_runtime_setup import Database
from test_application import TestCodec


class ChatDB(Database):
    def __init__(self):
        super().__init__();self.activities=[];self.activity_done=threading.Event()
        stamp=datetime.now(timezone.utc)
        self.conversation=(1,'fixture-agent','Fixture',None,None,stamp,stamp)
        self.fail_terminal=False
    def fetch_all(self,q):
        if 'FROM conversations ORDER BY' in q.sql:return [self.conversation]
        if 'FROM conversation_activities' in q.sql:
            stamp=datetime.now(timezone.utc)
            rows=[(i,*a[1:7],stamp,*a[8:12]) for i,a in enumerate(self.activities,1)]
            if 'ORDER BY id DESC' in q.sql:rows.reverse()
            return rows
        return super().fetch_all(q)
    def execute(self,q):
        if q.sql.startswith('UPDATE conversations'):return 1
        return super().execute(q)
    def fetch_one(self,q):
        if q.sql.startswith('INSERT INTO conversations('):
            self.conversation=(1,q.params[0],q.params[1],None,None,*self.conversation[-2:])
            return self.conversation
        if q.sql.startswith('SELECT id FROM conversations'):return (1,)
        if q.sql.startswith('SELECT') and 'FROM conversations WHERE' in q.sql:return self.conversation
        if q.sql.startswith('INSERT INTO conversation_activities'):
            if self.fail_terminal and q.params[2]!='user':raise RuntimeError('fixture DB failure')
            self.activities.append(q.params)
            if q.params[2]!='user':self.activity_done.set()
            return (len(self.activities),)
        return super().fetch_one(q)


class ChatSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=ChatDB();self.calls=[];self.hook=None;self.allowed=True
        def transport(context,url,headers,body,stream):
            self.calls.append(json.loads(body))
            if self.hook:self.hook(context)
            return Response(200,json.dumps({'choices':[{'message':{'content':'fixture answer'},'finish_reason':'stop'}],'usage':{'prompt_tokens':2,'completion_tokens':3}}))
        self.profiles=build_profile_runtime_application(self.db,b'x'*32,TestCodec(),transport_factory=lambda config:transport,health=Registry())
        key=self.profiles.profile_coordinator.save(LLMProfile(name='fixture',model='dummy',api_key='dummy-key',streaming=False))
        self.profiles.profile_coordinator.set_active(key)
        self.chat=build_conversation_runtime(self.db,self.profiles,transcript_root=self.temp.name,
            authorize=lambda req,conv:self.allowed,gate=lambda ctx,action:Grant(True,True,True),tools=(),
            system_prompt=lambda conv:'Fixture system prompt',single_process=True)
        self.addCleanup(lambda:self.chat.close(cancel=True,timeout=3))
        self.chat.service.register(self.profiles.application.router)
        self.token=self.request('/api/auth/init',{'password':'dummy-password'},authenticated=False)[1]['token']
    def request(self,path,data,authenticated=True,client='fixture-client'):
        raw=json.dumps(data).encode();env={'REQUEST_METHOD':'POST','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),'HTTP_IDEMPOTENCY_KEY':client}
        if authenticated:env['HTTP_AUTHORIZATION']='Bearer '+self.token
        status=[];body=b''.join(self.profiles.application(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]),json.loads(body)
    def test_actual_auth_accept_transcript_and_terminal_projection(self):
        path='/api/conversations/1/messages'
        self.assertEqual(self.request(path,{'message':'hello'},authenticated=False)[0],401)
        self.assertEqual(self.calls,[]);self.assertEqual(self.db.activities,[])
        self.assertEqual(self.request(path,{'message':'hello'}),(202,{'status':'accepted'}))
        self.assertTrue(self.db.activity_done.wait(3))
        drained=self.chat.close(timeout=3)
        self.assertTrue(drained.drained)
        self.assertEqual([a[2] for a in self.db.activities],['user','result'])
        self.assertEqual(self.db.activities[-1][7],'fixture answer')
        self.assertEqual(self.db.activities[-1][8:10],(2,3))
        self.assertEqual(len(self.calls),1)
        records=self.chat.transcript.store.read('conv-1')
        self.assertEqual(len(records),2)
    def test_authorization_denial_before_activity(self):
        self.allowed=False
        self.assertEqual(self.request('/api/conversations/1/messages',{'message':'hello'})[0],403)
        self.assertEqual(self.db.activities,[]);self.assertEqual(self.calls,[])
    def test_stop_inflight_and_drain(self):
        started=threading.Event();release=threading.Event()
        self.hook=lambda context:(started.set(),release.wait(3))
        self.assertEqual(self.request('/api/conversations/1/messages',{'message':'hello'})[0],202)
        self.assertTrue(started.wait(3))
        self.assertEqual(self.request('/api/conversations/1/stop',{}),(200,{'status':'stopping'}))
        self.assertFalse(self.chat.close(cancel=True,timeout=0).drained)
        release.set();self.assertTrue(self.db.activity_done.wait(3))
        self.assertTrue(self.chat.close(timeout=3).drained)
        self.assertTrue(self.db.activities[-1][5])
    def test_profile_identity_detects_private_configuration_change(self):
        conversation=self.chat.conversations.get(1)
        resources=self.chat.service.resolve(None,conversation)
        self.db.profiles[1]['api_key']='dummy-rotated-key'
        from artex.agent.chat import ChatUnavailable
        with self.assertRaises(ChatUnavailable):resources.resolver(resources.model)
        self.assertNotIn('dummy-key',resources.model.identity)
        self.assertEqual(self.calls,[])

    def test_retester_rejected_before_user_projection(self):
        self.db.conversation=(1,'retester',*self.db.conversation[2:])
        self.assertEqual(self.request('/api/conversations/1/messages',{'message':'hello'})[0],503)
        self.assertEqual(self.db.activities,[]);self.assertEqual(self.calls,[])

    def test_no_authority_defaults_or_mixed_database(self):
        count=len(self.db.calls)
        kwargs=dict(transcript_root=self.temp.name,authorize=lambda *a:True,
                    gate=lambda *a:Grant(),tools=(),system_prompt=lambda c:'fixture',single_process=True)
        with self.assertRaises(ValueError):build_conversation_runtime(ChatDB(),self.profiles,**kwargs)
        kwargs['single_process']=False
        with self.assertRaises(ValueError):build_conversation_runtime(self.db,self.profiles,**kwargs)
        self.assertEqual(len(self.db.calls),count)
        self.assertEqual(self.calls,[])

    def test_terminal_projection_failure_is_not_clean_drain(self):
        self.db.fail_terminal=True
        self.assertEqual(self.request('/api/conversations/1/messages',{'message':'hello'})[0],202)
        result=self.chat.close(timeout=3)
        self.assertFalse(result.drained)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.chat.transcript.store.read('conv-1')),2)
        self.assertEqual([a[2] for a in self.db.activities],['user'])


if __name__=='__main__':unittest.main()


class OneApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=ChatDB();self.calls=[]
        def transport(*args):
            self.calls.append(args)
            return Response(200,json.dumps({'choices':[{'message':{'content':'one app answer'},'finish_reason':'stop'}]}))
        self.kwargs=dict(transport_factory=lambda c:transport,health=Registry(),transcript_root=self.temp.name,
            authorize=lambda req,c:True,gate=lambda ctx,a:Grant(True,True,True),tools=(),
            agent_available=lambda key:key=='fixture-agent',system_prompt=lambda c:'fixture prompt',single_process=True)
        self.bundle=build_chat_application(self.db,b'x'*32,TestCodec(),**self.kwargs)
        self.addCleanup(lambda:self.bundle.close(cancel=True,timeout=3))
    def request(self,path,method='GET',data=None,token=None):
        raw=b'' if data is None else json.dumps(data).encode()
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),
             'wsgi.input':io.BytesIO(raw),'HTTP_IDEMPOTENCY_KEY':'one-app-client'}
        if token:env['HTTP_AUTHORIZATION']='Bearer '+token
        statuses=[];body=b''.join(self.bundle.application(env,lambda s,h:statuses.append(s)))
        return int(statuses[0].split()[0]),json.loads(body)
    def test_jwt_metadata_profile_chat_and_history_one_app(self):
        self.assertEqual(self.calls,[])
        self.assertEqual(self.request('/api/conversations')[0],401)
        token=self.request('/api/auth/init','POST',{'password':'dummy-password'})[1]['token']
        status,created=self.request('/api/conversations','POST',{'agent_key':'fixture-agent','title':'Fixture'},token)
        self.assertEqual(status,200);self.assertEqual(created['id'],1)
        for key in ('unknown','retester'):
            self.assertEqual(self.request('/api/conversations','POST',{'agent_key':key},token)[0],404)
        self.assertEqual(self.request('/api/conversations',token=token)[1]['conversations'][0]['running'],False)
        self.assertEqual(self.request('/api/llm/profiles','POST',{'name':'fixture','model':'dummy','api_key':'dummy-key','streaming':False},token)[0],200)
        self.assertEqual(self.request('/api/llm/profiles/active','POST',{'id':1},token)[0],200)
        self.assertEqual(self.request('/api/conversations/1/messages','POST',{'message':'hello'},token)[0],202)
        self.assertTrue(self.db.activity_done.wait(3));self.assertTrue(self.bundle.close(timeout=3).drained)
        status,history=self.request('/api/conversations/1/messages',token=token)
        self.assertEqual(status,200)
        self.assertEqual([a['kind'] for a in history['items']],['user','result'])
        self.assertFalse(history['running']);self.assertEqual(len(self.calls),1)
        self.assertEqual(self.request('/api/chat','POST',{'message':'task'},token)[0],404)
    def test_register_failure_cancels_and_drains_threads(self):
        from unittest.mock import patch
        from artex.server.chat import ChatService
        from artex.chat_dispatch import ChatDispatcher
        drained=[];original=ChatDispatcher.close
        def close(dispatcher,**kw):
            value=original(dispatcher,**kw);drained.append(value);return value
        with patch.object(ChatService,'register',side_effect=RuntimeError('registration fixture')),patch.object(ChatDispatcher,'close',close):
            with self.assertRaises(RuntimeError):build_chat_application(self.db,b'x'*32,TestCodec(),**self.kwargs)
        self.assertEqual(len(drained),1);self.assertTrue(drained[0].drained)
        self.assertEqual(self.calls,[])

class CatalogApplicationTests(OneApplicationTests):
    def test_catalog_create_conversation_and_rendered_prompt(self):
        from artex.db.agents import AgentStore
        from test_agents import ROW
        self.bundle.close(cancel=True,timeout=3)
        original_all=self.db.fetch_all;original_one=self.db.fetch_one;original_execute=self.db.execute
        catalog=[];prompts=[]
        def all_rows(q):
            if ' FROM agents' in q.sql and not q.sql.startswith('SELECT p.template_text'):return list(catalog)
            if 'GROUP BY' in q.sql and any(t in q.sql for t in ('agent_visibility','agent_skill_visibility','tools')):return []
            if 'SELECT p.template_text' in q.sql:return [(prompts[0],)] if prompts else []
            if any(t in q.sql for t in ('FROM agent_prompt_vars','FROM agent_prompts','FROM agent_visibility','FROM agent_skill_visibility')):return []
            if q.sql=='SELECT id,name,model,is_default FROM llm_profiles ORDER BY id':return []
            return original_all(q)
        def one(q):
            if q.sql.startswith('INSERT INTO agents('):
                row=list(ROW);row[1:4]=q.params;catalog.append(tuple(row));return tuple(row)
            if q.sql.startswith('INSERT INTO agent_prompts('):prompts.append(q.params[1]);return (7,)
            return original_one(q)
        def execute(q):
            if q.sql.startswith('UPDATE agents SET current_prompt_id'):return 1
            return original_execute(q)
        self.db.fetch_all=all_rows;self.db.fetch_one=one;self.db.execute=execute
        kwargs=dict(self.kwargs,agent_available=lambda key:key=='custom',catalog_store=AgentStore(self.db),
                    catalog_prompt=lambda agent,stored,conv,base:base+'\n'+stored)
        self.bundle=build_chat_application(self.db,b'x'*32,TestCodec(),**kwargs)
        self.assertEqual(self.request('/api/agents')[0],401)
        token=self.request('/api/auth/init','POST',{'password':'dummy-password'})[1]['token']
        self.assertEqual(self.request('/api/agents',token=token),(200,{'agents':[]}))
        self.assertEqual(self.request('/api/agents','POST',{'key':'custom','name':'Custom'},token)[0],200)
        self.assertEqual(self.request('/api/agents',token=token)[1]['agents'][0]['key'],'custom')
        self.assertEqual(self.request('/api/conversations','POST',{'agent_key':'custom'},token)[0],200)
        self.assertEqual(self.request('/api/llm/profiles','POST',{'name':'fixture','model':'dummy','api_key':'dummy-key','streaming':False},token)[0],200)
        self.assertEqual(self.request('/api/llm/profiles/active','POST',{'id':1},token)[0],200)
        self.assertEqual(self.request('/api/conversations/1/messages','POST',{'message':'hello'},token)[0],202)
        self.assertTrue(self.db.activity_done.wait(3));self.assertTrue(self.bundle.close(timeout=3).drained)
        sent=json.loads(self.calls[0][3])
        self.assertIn(prompts[0],sent['messages'][0]['content'])
        self.assertEqual(len(catalog),1)  # explicit create only, no builtins
