import unittest
from artex.agent.prompt_render import (builtin_templates,custom_seed_templates,custom_default_template,
    render_chat_body,render_body,PromptRenderError,CATALOG)
from artex.agent.additional_prompts import PENTEST_DEFAULT,REPORTER_DEFAULT_PROMPT,DEFAULT_ASSISTANT_PROMPT
from artex.db.catalog_seed import BUILTINS

class SeedMappingTests(unittest.TestCase):
    def test_exact_builtin_source_keys_and_no_reporter_promotion(self):
        self.assertEqual(set(builtin_templates()),{item[0] for item in BUILTINS})
        self.assertNotIn('reporter',builtin_templates())
        self.assertEqual(custom_seed_templates(),{'reporter':REPORTER_DEFAULT_PROMPT})
        self.assertEqual(builtin_templates()['pentest'],PENTEST_DEFAULT)
        self.assertEqual(custom_default_template(),DEFAULT_ASSISTANT_PROMPT)
    def test_chat_fallback_is_generic_for_every_identity(self):
        for key in ('reporter','pentest','auto','custom','my-agent'):
            for stored in (None,'','{{.Unknown}}','{{range .Now}}bad{{end}}'):
                self.assertEqual(render_chat_body(key,{},stored_template=stored),DEFAULT_ASSISTANT_PROMPT)
    def test_seed_not_runtime_default(self):
        self.assertEqual(render_body('pentest',{}),PENTEST_DEFAULT)
        self.assertNotEqual(render_chat_body('pentest',{}),PENTEST_DEFAULT)
        self.assertEqual(render_chat_body('reporter',{},stored_template=REPORTER_DEFAULT_PROMPT),REPORTER_DEFAULT_PROMPT)
    def test_explicit_stored_template_and_universal_variables(self):
        value='{{.DataDir}} at {{.Now}}'
        self.assertEqual(render_chat_body('actual-key',{'DataDir':'/fixture','Now':'time'},stored_template=value),'/fixture at time')
        self.assertEqual(render_chat_body('reporter',{'Now':'t'},stored_template='{{.Goal}}'),DEFAULT_ASSISTANT_PROMPT)
        for key in ('pentest','reporter'):self.assertEqual(CATALOG[key],frozenset(('DataDir','Now')))
    def test_source_whitespace_template_is_not_empty(self):
        self.assertEqual(render_chat_body('reporter',{},stored_template=' \n'),' \n')
    def test_returns_copies_and_no_implicit_registration(self):
        mapping=custom_seed_templates();mapping['reporter']='changed'
        self.assertEqual(custom_seed_templates()['reporter'],REPORTER_DEFAULT_PROMPT)
        with self.assertRaises(PromptRenderError):render_chat_body('',{})
        with self.assertRaises(PromptRenderError):render_body('reporter',{})

if __name__=='__main__':unittest.main()
