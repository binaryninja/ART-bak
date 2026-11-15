"""Text-contract tests, not model behavior or authorization tests."""
import unittest
from datetime import datetime,timezone
from artex.agent.prompts import Constraint
from artex.agent.planner_prompts import planner_system,goals_messages,auto_system

NOW=datetime(2026,11,15,tzinfo=timezone.utc)

class PlannerPromptTests(unittest.TestCase):
    def test_planner_full_decision_contract(self):
        text=planner_system('Verify only approved targets','/data','/work',now=NOW)
        for marker in ('frontier_open=0','running_intents','prove_goal(goal_id, evidence_id, reason)',
                       'coverage.pct=40%','at most ONCE','at most 3 probes','facts>0',
                       'done means','exhausted means','blocked means','material new mechanism',
                       'get_worker_trace / get_worker_output','TodoWrite','at most 4',
                       'summary:','asset_ids:','parent_ids:','type/company_id/task_id',
                       'shared globally','Diversity ALWAYS obeys operation constraints'):
            self.assertIn(marker,text)
        self.assertIn('Verify only approved targets',text)
        self.assertNotIn('{{.Goal}}',text)

    def test_planner_override_preserves_constraints_artifacts(self):
        text=planner_system('goal','/data','/work',override='custom {{.Goal}}',now=NOW,
                            constraints=[Constraint('deny','No traffic outside lab')])
        self.assertTrue(text.startswith('custom goal'))
        self.assertIn('Intermediate artifact rules',text)
        self.assertIn('No traffic outside lab',text)
        self.assertIn('does NOT grant authorization',text)
        with self.assertRaises(ValueError): planner_system('g','d','w',constraints=None)

    def test_planner_catalog_and_fallback(self):
        text=planner_system('goal','data','work',override='{{.Scope}}|{{.AssetSummary}}|{{.Now}}',now=NOW)
        self.assertTrue(text.startswith('||2026-11-15'))
        for override in ('{{.Missing}}','{{if .Goal}}oops{{end}}'):
            self.assertTrue(planner_system('g','d','w',override=override,now=NOW).startswith('You are the planner'))

    def test_goal_constraints_before_deliverables(self):
        system,user=goals_messages('Final result','Target example.test, no port scans','/data',now=NOW)
        for marker in ('BEFORE splitting goals','set_constraints','type=deny','type=allow',
                       'self-contained','concrete targets','ONLY constraints explicitly',
                       'NEVER invent goals','vulnclass','set_goals'):
            self.assertIn(marker,system)
        self.assertNotIn('add_task_scope',system)
        self.assertIn('Final result',user)
        self.assertIn('rules of engagement',user)
        self.assertIn('Target example.test, no port scans',user)
        self.assertNotIn('Target example.test, no port scans',system)

    def test_scope_tail_cannot_be_removed_by_body(self):
        system,_=goals_messages('g','d','data',override='custom',scope_tool_available=True,now=NOW)
        self.assertTrue(system.startswith('custom'))
        for marker in ('ONLY the exact target','NEVER expand','COMPLETE hostname',
                       'NOT example.net','kind=subdomain','kind=root_domain',
                       'kind=ip or cidr','Do not register company','reason',
                       'BEFORE submitting goals'):
            self.assertIn(marker,system)
        with self.assertRaises(ValueError): goals_messages('g','d','data',scope_tool_available=1)

    def test_description_is_not_duplicated_into_template(self):
        system,user=goals_messages('goal','Description once','data',
                                  override='{{.EngagementDescription}}|{{.DataDir}}',now=NOW)
        self.assertEqual(system,'|data')
        self.assertEqual(user.count('Description once'),1)
        _,user=goals_messages('goal','  ','data',now=NOW)
        self.assertEqual(user,'Task goal:\ngoal')

    def test_auto_tools_and_scope(self):
        text=auto_system(now=NOW)
        for marker in ('Do not perform penetration testing yourself','list_tasks','spawn_task',
                       'get_task_graph','list_task_findings','get_task_worker_trace','pause_task',
                       'add_task_hint','create_skill / update_skill',
                       'create_custom_tool / update_custom_tool','create_mcp / update_mcp',
                       'authorized scope','real tool results'):
            self.assertIn(marker,text)

    def test_values_not_evaluated_as_templates(self):
        value='{{.Now}} <untrusted>'
        text=planner_system(value,'d','w',override='{{.Goal}}',now=NOW)
        self.assertTrue(text.startswith(value))
        with self.assertRaises(ValueError): goals_messages(None,'d','data',now=NOW)

if __name__=='__main__': unittest.main()
