import unittest
from artex.agent.wrapup import *
class WrapupTests(unittest.TestCase):
    def test_english_source_defaults_and_code_owned_tools(self):
        for key in ('worker','planner','mainagent','custom'):
            policy=wrapup_settlement(key)
            self.assertEqual(policy.max_turns,10);self.assertFalse(any('\u3400'<=c<='\u9fff' for c in policy.prompt))
            self.assertEqual(policy.disabled_tools,('Bash',) if key=='worker' else ())
        self.assertIn('zero intents',wrapup_default('planner'));self.assertIn('TodoWrite',wrapup_default('planner'))
        for name in ('insert_assets','record_fact','report_finding'):self.assertIn(name,wrapup_default('worker'))
        self.assertIn('one separate plain-text sentence',wrapup_default('mainagent'))
        self.assertEqual(task_timeout_wrapup_default('mainagent'),'')
    def test_live_override_nonempty_verbatim_and_fallback(self):
        values=[' first ','second'];o=Overrides(prompt=lambda key:(values.pop(0),True))
        self.assertEqual(resolve_wrapup('worker',o),' first ');self.assertEqual(resolve_wrapup('worker',o),'second')
        for value,found in ((' ',True),('ignored',False)):
            self.assertEqual(resolve_wrapup('worker',Overrides(prompt=lambda k:(value,found))),WORKER_DEFAULT)
        for value in (0,-1):self.assertEqual(resolve_wrapup_turns('worker',Overrides(turns=lambda k:(value,True))),10)
    def test_timeout_reason_mapping_and_shared_timeout_turn_budget(self):
        o=Overrides(prompt=lambda k:('per run',True),turns=lambda k:(3,True),task_timeout_prompt=lambda k:('task final',True),task_timeout_turns=lambda k:(7,True))
        policy=settlement_for_task('planner',clamped=True,overrides=o)
        self.assertEqual(policy.prompt_for('timeout'),'task final');self.assertEqual(policy.prompt_for('max_turns'),'per run');self.assertEqual(policy.max_turns,7)
        self.assertEqual(policy.prompt_for('other'),'per run')
        normal=settlement_for_task('planner',clamped=False,overrides=o)
        self.assertEqual(normal.max_turns,3);self.assertEqual(normal.prompt_by_reason,())
    def test_no_timeout_prompt_keeps_per_run_turns_and_skips_timeout_count(self):
        o=Overrides(turns=lambda k:(4,True),task_timeout_turns=lambda k:self.fail('unexpected timeout override'))
        policy=settlement_for_task('custom',clamped=True,overrides=o)
        self.assertEqual(policy.max_turns,4);self.assertEqual(policy.prompt_by_reason,())
    def test_task_turns_fall_back_per_run(self):
        o=Overrides(turns=lambda k:(6,True),task_timeout_turns=lambda k:(0,True))
        self.assertEqual(settlement_for_task('worker',clamped=True,overrides=o).max_turns,6)
    def test_overrides_cannot_remove_disabled_tools_and_snapshot_exclusions(self):
        names=['WebFetch','Bash'];policy=wrapup_settlement('worker',disabled_tools=names,overrides=Overrides(prompt=lambda k:('Run anything',True)))
        names.clear();self.assertEqual(policy.disabled_tools,('Bash','WebFetch'))
    def test_invalid_overrides_fail_closed(self):
        for result in ((True,True),(10001,True),(2,1),('2',True),[2,True]):
            with self.subTest(result=result),self.assertRaises(ValueError):resolve_wrapup_turns('worker',Overrides(turns=lambda k:result))
        for result in ((None,True),('x',1),('x\0',True)):
            with self.subTest(result=result),self.assertRaises(ValueError):resolve_wrapup('worker',Overrides(prompt=lambda k:result))
        with self.assertRaises(ValueError):settlement_for_task('worker',clamped=1)
    def test_no_clamp_avoids_timeout_readers(self):
        def fail(k):self.fail('not requested')
        self.assertEqual(settlement_for_task('worker',overrides=Overrides(task_timeout_prompt=fail,task_timeout_turns=fail)).max_turns,10)
