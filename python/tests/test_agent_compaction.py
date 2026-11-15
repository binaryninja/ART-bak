from copy import deepcopy
import unittest
from artex.agent.compaction import Budget, WorkingCompactor, CompactionError, api_rounds, estimate_tokens
from artex.agent.runtime import Grant, RunStopped
from test_process import Context


def user(text):return {'role':'user','content':[{'type':'text','text':text}]}
def assistant(text):return {'role':'assistant','content':[{'type':'text','text':text}]}
def history():return [user('historical evidence '*1000),assistant('old answer '*500),user('old followup '*500),assistant('recent answer'),user('current question')]
class Provider:
    def __init__(self):self.calls=[];self.hook=None;self.result=(assistant('Old observations, pending question remains.'),'end_turn',{'input_tokens':3,'output_tokens':8})
    def complete(self,context,request):
        self.calls.append(deepcopy(request))
        if self.hook:self.hook()
        if isinstance(self.result,Exception):raise self.result
        return self.result

class CompactionTests(unittest.TestCase):
    def setUp(self):self.provider=Provider();self.context=Context();self.live=True;self.grants=[]
    def compactor(self,**kw):
        def gate(c,a):self.grants.append(a);return Grant(True,True,True)
        return WorkingCompactor(self.provider,gate=kw.pop('gate',gate),current=lambda:self.live,budget=kw.pop('budget',Budget(keep_recent=2)),**kw)
    def test_source_utf8_estimator(self):
        blocks=[{'type':'text','text':'é中'}, {'type':'thinking','thinking':'abcd'},
                {'type':'tool_use','id':'x','name':'f','input':'{"x":1}'},
                {'type':'tool_result','tool_use_id':'x','content':[{'type':'text','text':'12345678'}]}]
        self.assertEqual(estimate_tokens([{'content':blocks}]),(5+4+1+7+8)//4)
        self.assertEqual(estimate_tokens([user('123'),user('4')]),1)
    def test_source_thresholds(self):
        b=Budget(0,0,0);self.assertEqual((b.context_window,b.reserved,b.keep_recent),(200000,8000,8))
        self.assertEqual((b.effective,b.buffer,b.auto_threshold,b.micro_threshold),(192000,13000,179000,115200))
        self.assertEqual(Budget(420000,20000).buffer,30000)
        self.assertEqual(Budget(820000,20000).buffer,50000)
        self.assertEqual(b.count([user('x')],12345),12345)
        self.assertTrue(b.needs_summary([],180000))
    def test_whole_round_pairing(self):
        messages=[user('start'),{'role':'assistant','content':[{'type':'tool_use','id':'a','name':'f','input':{}}]},
                  {'role':'user','content':[{'type':'tool_result','tool_use_id':'a','content':[{'type':'text','text':'result'}]}]},assistant('done')]
        groups=api_rounds(messages)
        self.assertEqual([len(g) for g in groups],[1,2,1])
        with self.assertRaises(ValueError):api_rounds(messages[:2])
    def test_raw_untouched_no_replay_and_tail_preserved(self):
        raw=history();before=deepcopy(raw)
        result=self.compactor().compact(self.context,raw,force=True)
        self.assertEqual(raw,before);self.assertEqual(result.raw_messages,before)
        self.assertEqual(result.messages[-2:],before[-2:]);self.assertEqual(result.summarized_messages,3)
        self.assertEqual(len(self.provider.calls),1);self.assertEqual(len(self.grants),1)
        self.assertEqual(self.provider.calls[0]['tools'],[])
        self.assertIn('not new evidence',result.messages[0]['content'][0]['text'])
        result.messages[-1]['content'][0]['text']='mutated'
        self.assertEqual(raw,before)
    def test_small_context_no_provider(self):
        result=self.compactor().compact(self.context,[user('hello')])
        self.assertFalse(result.changed);self.assertFalse(self.provider.calls)
    def test_static_overhead_cannot_silently_pass_unchanged(self):
        with self.assertRaises(CompactionError):
            self.compactor().compact(self.context,[user('hello')],static_tokens=200000)
        self.assertFalse(self.provider.calls)

    def test_gate_fails_closed(self):
        for grant in (None,True,Grant(True,False,True),Grant(1,True,True)):
            with self.assertRaises(RunStopped):self.compactor(gate=lambda c,a:grant).compact(self.context,history(),force=True)
        self.assertFalse(self.provider.calls)
    def test_scope_change_during_gate(self):
        def gate(c,a):self.live=False;return Grant(True,True,True)
        with self.assertRaises(RunStopped):self.compactor(gate=gate).compact(self.context,history(),force=True)
        self.assertFalse(self.provider.calls)
    def test_cancel_or_scope_change_after_provider(self):
        for mutate in (lambda:setattr(self.context,'cancelled',True),lambda:setattr(self,'live',False)):
            self.context=Context();self.live=True;self.provider.hook=mutate
            with self.assertRaises((RuntimeError,RunStopped)):self.compactor().compact(self.context,history(),force=True)
        self.assertEqual(len(self.provider.calls),2)
    def test_failed_or_partial_summary_not_retried(self):
        for value in (RuntimeError('fixture'),(assistant('partial'),'max_tokens',{}),
                      (assistant('no'),'refusal',{}),({'role':'assistant','content':[{'type':'tool_use','id':'x','name':'f','input':{}}]},'tool_use',{})):
            self.provider.result=value
            with self.assertRaises((RuntimeError,CompactionError)):self.compactor().compact(self.context,history(),force=True)
        self.assertEqual(len(self.provider.calls),4)
    def test_admission_and_output_bounds(self):
        with self.assertRaises(CompactionError):self.compactor(max_request_bytes=5).compact(self.context,history(),force=True)
        with self.assertRaises(CompactionError):self.compactor(budget=Budget(100,50,2)).compact(self.context,history(),force=True)
        self.assertFalse(self.provider.calls)
        with self.assertRaises(CompactionError):self.compactor(max_summary_bytes=3).compact(self.context,history(),force=True)
    def test_unreducible_tail_and_summary(self):
        with self.assertRaises(CompactionError):self.compactor().compact(self.context,[user('one')],force=True)
        self.provider.result=(assistant('larger'*10000),'end_turn',{})
        with self.assertRaises(CompactionError):self.compactor().compact(self.context,history(),force=True)
    def test_summary_markup_is_stripped(self):
        self.provider.result=(assistant('<analysis>scratch</analysis><summary>Facts only</summary>'),'end_turn',{})
        result=self.compactor().compact(self.context,history(),force=True)
        self.assertIn('Summary:\nFacts only',result.messages[0]['content'][0]['text'])
        self.assertNotIn('scratch',result.messages[0]['content'][0]['text'])

if __name__=='__main__':unittest.main()
