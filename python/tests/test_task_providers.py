"""Stored-role selection and inert HTTP only; no real credentials or providers."""
import unittest,json
from artex.providers import Response
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from artex.agent.task_providers import *
from artex.db.profiles import LLMProfile
from artex.agent.goals import GoalScope
from artex.agent.runtime import RunStopped

class Context:
    cancelled=False
    def check(self):
        if self.cancelled:raise RunStopped('cancelled')
class DB:
    def fetch_all(self,q):return [(3,)]
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.tasks=TaskStore(self.db);self.agents=AgentStore(self.db);self.profiles=ProfileStore(self.db)
        self.task=SimpleNamespace(id=5,exploration_id=9,timeout_seconds=0,llm_profile_ids=[2],active_llm_profile_id=2)
        self.tasks.get=lambda id:self.task
        from artex.db.agents import Agent
        self.agent=Agent(1,'goals','Goals','','goals',True,True,8,600,False,False,'',0,'',0,'serial','all',1,None)
        self.agents.get=lambda role:replace(self.agent,key=role,role=role)
        self.rows={i:LLMProfile(id=i,name=f'p{i}',model=f'm{i}',api_key='inert-not-a-key',base_url='https://example.invalid/v1',streaming=False) for i in (1,2,3)}
        self.reads=[];self.allowed=True;self.live=True;self.construct=[];self.calls=[]
        def get(id):self.reads.append(id);return self.rows.get(id)
        self.profiles.get=get
        def transport(config):
            self.construct.append(config.model)
            def invoke(*args,**kwargs):
                self.calls.append(1)
                return Response(200,json.dumps({'choices':[{'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}}))
            return invoke
        self.factory=ProfileFactory(transport)
        self.resolver=TaskProviderResolver(self.tasks,self.agents,self.profiles,self.factory,current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),authorize_task=lambda s,r:True,authorize_profile=lambda s,r,i:self.allowed,allow_global=True)
        self.snapshot={'task_id':5,'exploration_id':9}
    def binding(self):
        model=self.resolver.select(self.snapshot,'goals').model
        scope=GoalScope(5,9,71,'r1',model,'inert system')
        return self.resolver.bind(scope,'goals',model,current=lambda s:self.live,lifecycle_guard=lambda s:nullcontext())
    def test_select_no_transport_and_provisional_equivalence(self):
        a=self.resolver.select({'provisional':True,'options':TaskCreateOptions(llm_profile_ids=[2])},'goals')
        b=self.resolver.select(self.snapshot,'goals')
        self.assertEqual(a,b);self.assertEqual(self.construct,[]);self.assertEqual(self.calls,[])
        self.assertNotIn('inert-not-a-key',repr(a))
    def test_distinct_role_and_agent_precedence(self):
        self.agent=replace(self.agent,llm_profile_id=1)
        self.assertEqual(self.resolver.select(self.snapshot,'goals').model.profile_id,1)
        self.assertNotEqual(self.resolver.select(self.snapshot,'goals').model.identity,self.resolver.select(self.snapshot,'planner').model.identity)
    def test_exhaustion_and_invalid_chain_never_global(self):
        self.task.active_llm_profile_id=None
        with self.assertRaises(TaskProviderUnavailable):self.resolver.select(self.snapshot,'goals')
        self.assertEqual(self.reads,[])
        self.task.active_llm_profile_id=2;del self.rows[2]
        with self.assertRaises(TaskProviderUnavailable):self.resolver.select(self.snapshot,'goals')
        self.assertNotIn(3,self.reads)
    def test_credential_read_requires_grant(self):
        self.allowed=False
        with self.assertRaises(TaskProviderUnavailable):self.resolver.select(self.snapshot,'goals')
        self.assertEqual(self.reads,[])
    def test_missing_role_denied(self):
        self.agents.get=lambda role:None
        with self.assertRaises(TaskProviderUnavailable):self.resolver.select(self.snapshot,'goals')
    def test_actual_complete_only_after_bound_call(self):
        binding=self.binding();self.assertEqual(self.construct,[])
        binding.provider.complete(Context(),{'messages':[{'role':'user','content':[{'type':'text','text':'hello'}]}]})
        self.assertEqual(self.calls,[1])
    def test_profile_change_or_owner_cancel_before_transport(self):
        binding=self.binding();self.rows[2]=replace(self.rows[2],model='changed')
        with self.assertRaises(TaskProviderUnavailable):binding.provider.complete(Context(),{})
        self.assertEqual(self.calls,[])
        self.rows[2]=replace(self.rows[2],model='m2');self.live=False
        with self.assertRaises(TaskProviderUnavailable):binding.provider.complete(Context(),{})
    def test_transport_construction_revocation_prevents_call(self):
        binding=self.binding();original=self.factory._transport_factory
        def make(config):
            transport=original(config);self.live=False;return transport
        self.factory._transport_factory=make
        with self.assertRaises(TaskProviderUnavailable):binding.provider.complete(Context(),{})
        self.assertEqual(self.calls,[])
    def test_read_callback_swap_fails_closed(self):
        old=self.profiles.get
        def get(id):
            value=old(id);self.resolver.authorize_profile=lambda *a:True;return value
        self.profiles.get=get
        with self.assertRaises(TaskProviderUnavailable):self.resolver.select(self.snapshot,'goals')
    def test_global_requires_optin(self):
        self.task.llm_profile_ids=[];self.task.active_llm_profile_id=None
        self.assertEqual(self.resolver.select(self.snapshot,'goals').model.profile_id,3)
        r=TaskProviderResolver(self.tasks,self.agents,self.profiles,self.factory,current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),authorize_task=lambda s,r:True,authorize_profile=lambda *a:True)
        with self.assertRaises(TaskProviderUnavailable):r.select(self.snapshot,'goals')

    def test_stream_close_revocation_blocks_completion(self):
        self.rows[2]=replace(self.rows[2],streaming=True)
        closed=[]
        def factory(config):
            def transport(*args):
                data=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                def close():closed.append(1);self.live=False
                return Response(200,data,close)
            return transport
        self.factory._transport_factory=factory
        binding=self.binding()
        with self.assertRaises(TaskProviderUnavailable):list(binding.provider.stream(Context(),{'messages':[]}))
        self.assertEqual(closed,[1])
    def test_stream_cancel_preserved_and_closed(self):
        self.rows[2]=replace(self.rows[2],streaming=True);ctx=Context();closed=[]
        def factory(config):
            def transport(*args):
                def chunks():
                    yield b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
                    ctx.cancelled=True
                    raise RunStopped('cancelled')
                def close():closed.append(1);self.live=False
                return Response(200,chunks(),close)
            return transport
        self.factory._transport_factory=factory
        binding=self.binding()
        with self.assertRaises(RunStopped):list(binding.provider.stream(ctx,{'messages':[]}))
        self.assertEqual(closed,[1])
    def test_provider_wrapper_policy_swap_denied(self):
        binding=self.binding();binding.provider.current=lambda s:True
        with self.assertRaises(TaskProviderUnavailable):binding.provider.complete(Context(),{})
        self.assertEqual(self.calls,[])
