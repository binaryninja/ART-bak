"""Independent source-contract checks for English prompt roles and boundaries."""
import re,unittest
from artex.agent.additional_prompts import PENTEST_DEFAULT,REPORTER_DEFAULT_PROMPT,DEFAULT_ASSISTANT_PROMPT,additional_templates
from artex.agent import prompt_render as rendering

class AdditionalPromptReview(unittest.TestCase):
    def test_pentest_scope_verification_and_wrapup_contracts(self):
        for text in ('Never touch out-of-scope targets','independent check','immediately stop all probes and commands','Do not start new actions','Never guess IDs','do not establish task ownership','Do not substitute CVE lookup'):
            self.assertIn(text,PENTEST_DEFAULT)
        for tool in ('TodoWrite','insert_assets','list_assets','report_finding','list_findings','traffic_search','traffic_get','traffic_refs'):
            self.assertIn(tool,PENTEST_DEFAULT)
    def test_reporter_identity_version_and_no_reprobe_contract(self):
        for text in ('do not perform penetration tests or exploitation','exploration node ID','standalone finding record ID','finding_id=<node_id>','evidence_version=<the version actually read>','reread and regenerate','never guess it','repeat probes merely','Once update_finding_report succeeds, stop'):
            self.assertIn(text,REPORTER_DEFAULT_PROMPT)
    def test_english_bodies_are_plain_and_separate_from_host_tails(self):
        values=additional_templates()
        self.assertEqual(set(values),{'pentest','reporter','custom'})
        for body in values.values():
            self.assertFalse(re.search('[\u3400-\u9fff]',body))
            self.assertNotIn('{{',body)
        values['custom']='caller edit'
        self.assertEqual(additional_templates()['custom'],DEFAULT_ASSISTANT_PROMPT)
    def test_builtin_seed_map_matches_upstream_not_custom_role_aliases(self):
        seeds=rendering.builtin_templates()
        self.assertEqual(set(seeds),{'goals','planner','mainagent','worker','auto','pentest'})
        self.assertEqual(seeds['pentest'],PENTEST_DEFAULT)
        self.assertNotIn('reporter',seeds);self.assertNotIn('custom',seeds)
    def test_additional_roles_render_chat_vars_without_recursive_expansion(self):
        for role in ('pentest','reporter','custom'):
            rendered=rendering.render('{{.DataDir}} {{.Now}}',{'DataDir':'{{.Goal}}','Now':'fixture'},role=role)
            self.assertEqual(rendered,'{{.Goal}} fixture')
            with self.assertRaises(rendering.PromptRenderError):rendering.render('{{.Goal}}',{'Goal':'unauthorized'},role=role)
