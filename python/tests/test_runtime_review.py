"""Independent regression cases. Every provider/tool is inert and injected."""
import json
import unittest
from artex.agent.runtime import BoundedAgent, Grant, Tool
from artex.providers import Config, Provider, Response
from artex.manager import CancellationToken


def request():
    return {'messages': [{'role':'user','content':[{'type':'text','text':'inert test'}]}]}


def tool_call():
    return {'type':'tool_use','id':'one','name':'inert','input':{}}


def grant(context, action):
    return Grant(True,True,True)


class Stub:
    def __init__(self, content, stop='end_turn'):
        self.content, self.stop, self.count = content, stop, 0
    def complete(self, context, req):
        self.count += 1
        if self.count > 1:
            return {'role':'assistant','content':[{'type':'text','text':'done'}]},'end_turn',{}
        return {'role':'assistant','content':self.content},self.stop,{}


class RuntimeReviewTests(unittest.TestCase):
    def make_tool(self, calls):
        return Tool('inert','Inert test-only callback',{'type':'object'},
                    lambda ctx,args: calls.append(args) or 'ok',lambda args: True)

    def test_malformed_text_prevents_tool_execution(self):
        for block in ({'type':'text','text':7},{'type':'thinking','thinking':[]},
                      {'type':'text'}, {'type':'text','text':'\ud800'}):
            with self.subTest(block=repr(block)):
                calls=[]
                result=BoundedAgent(Stub([block,tool_call()],'tool_use'),gate=grant,
                                    tools=[self.make_tool(calls)]).run(request())
                self.assertEqual(result.reason,'invalid_protocol')
                self.assertEqual(calls,[])

    def test_unknown_stop_prevents_tool_execution(self):
        calls=[]
        result=BoundedAgent(Stub([tool_call()],'provider_unknown_status'),gate=grant,
                            tools=[self.make_tool(calls)]).run(request())
        self.assertEqual(result.reason,'invalid_protocol')
        self.assertEqual(calls,[])

    def test_concrete_refusal_signal_survives_adapter(self):
        calls=[]
        body={'choices':[{'message':{'refusal':'Cannot comply',
               'tool_calls':[{'id':'one','type':'function','function':{'name':'inert','arguments':'{}'}}]},
               'finish_reason':'tool_calls'}]}
        transport=lambda *args: Response(200,json.dumps(body))
        provider=Provider(Config('openai','test-model'),transport)
        result=BoundedAgent(provider,gate=grant,tools=[self.make_tool(calls)]).run(request())
        self.assertEqual(result.reason,'refused')
        self.assertEqual(calls,[])

    def test_validator_cancellation_is_rechecked_before_tool(self):
        calls=[]; token=CancellationToken()
        def validate(args):
            token.cancel('paused_by_user')
            return True
        tool=Tool('inert','Inert test-only callback',{},lambda ctx,args:calls.append(args) or 'ok',validate)
        result=BoundedAgent(Stub([tool_call()],'tool_use'),gate=grant,tools=[tool]).run(request(),token=token)
        self.assertEqual(result.reason,'cancelled')
        self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
