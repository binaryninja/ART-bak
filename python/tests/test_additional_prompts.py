import re
import unittest
from artex.agent.additional_prompts import PENTEST_DEFAULT,REPORTER_DEFAULT_PROMPT,DEFAULT_ASSISTANT_PROMPT,additional_templates
from artex.agent.prompt_render import render

class AdditionalPromptTests(unittest.TestCase):
    def test_roles_are_explicit_seed_candidates_only(self):
        seeds=additional_templates()
        self.assertEqual(set(seeds),{'pentest','reporter','custom'})
        seeds['pentest']='changed'
        self.assertEqual(additional_templates()['pentest'],PENTEST_DEFAULT)
    def test_all_bodies_english_and_no_unsupported_directives(self):
        for body in additional_templates().values():
            self.assertIsNone(re.search('[\u3400-\u9fff]',body))
            self.assertNotIn('{{',body)
            self.assertEqual(render(body,{'Now':'fixture','DataDir':'fixture'},role='custom'),body)
    def test_pentest_scope_verification_and_wrapup(self):
        for phrase in ('Never touch out-of-scope targets','independent command','unconfirmed',
                       'Wrap-up has highest priority','immediately stop all probes and commands',
                       'Do not start new actions','Do not substitute CVE lookup',
                       'Never guess IDs or repeat probes','materially new mechanism','Write only new information'):
            self.assertIn(phrase,PENTEST_DEFAULT)
    def test_pentest_source_tool_contracts(self):
        for name in ('TodoWrite','insert_assets','list_assets','report_finding','list_findings','traffic_search','traffic_get','traffic_refs','evidence'):
            self.assertIn(name,PENTEST_DEFAULT)
        for number in range(1,7):self.assertIn(f'\n{number}. ',PENTEST_DEFAULT)
    def test_reporter_no_execution_and_identifier_distinction(self):
        for phrase in ('You do not perform penetration tests or exploitation',
                       'standalone finding record ID used by get_finding_traffic',
                       'Never confuse the two finding identifiers',
                       'update_finding_report(finding_id=<node_id>',
                       'If you cannot extract node_id','omit evidence_version; never guess it'):
            self.assertIn(phrase,REPORTER_DEFAULT_PROMPT)
    def test_reporter_evidence_version_conflict_and_success(self):
        for phrase in ('reread and regenerate','do not simply substitute a new version and retry',
                       'Do not fabricate requests/responses or repeat probes',
                       'A report that was not saved is not completed work',
                       'Once update_finding_report succeeds, stop'):
            self.assertIn(phrase,REPORTER_DEFAULT_PROMPT)
    def test_reporter_all_source_sections_and_tools(self):
        self.assertEqual(REPORTER_DEFAULT_PROMPT.count('`## '),8)
        for name in ('get_task_node_detail','get_finding_traffic','list_task_worker_traces','get_task_worker_trace',
                     'search_task_worker_traces','get_task_graph','list_task_findings','update_finding_report',
                     'binding_id','evidence_version','vulnclass'):
            self.assertIn(name,REPORTER_DEFAULT_PROMPT)
    def test_custom_scope_and_language(self):
        self.assertIn('in English',DEFAULT_ASSISTANT_PROMPT)
        self.assertIn('Do only what the user requested',DEFAULT_ASSISTANT_PROMPT)
        self.assertIn('do not invent information',DEFAULT_ASSISTANT_PROMPT)

if __name__=='__main__':unittest.main()
