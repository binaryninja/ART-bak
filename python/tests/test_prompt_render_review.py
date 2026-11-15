"""Independent source-supported prompt subset regressions; no template execution."""
import unittest
from artex.agent.prompt_render import render, render_body, builtin_templates, CATALOG, PromptRenderError
from artex.agent.prompts import render_system
from artex.agent.planner_prompts import _render
from artex.agent.additional_prompts import PENTEST_DEFAULT


class PromptRenderReview(unittest.TestCase):
    def test_builtin_bodies_equal_existing_english_builders(self):
        for role, template in builtin_templates().items():
            values = {key:'literal <&> {{.Now}} '+key for key in CATALOG[role]}
            if role=='pentest':
                expected=PENTEST_DEFAULT
                for key,value in values.items():expected=expected.replace('{{.'+key+'}}',value)
            else:
                expected = (render_system(role,template,values) if role in ('mainagent','worker')
                            else _render(role,template,values,None))
            self.assertEqual(render_body(role,values),expected)

    def test_invalid_comment_spacing_rejects_including_inactive_arm(self):
        # Go text/template/parse/lex.go lexLeftDelim/lexComment require exact
        # delimiter placement; comment whitespace is not general action space.
        for comment in ('{{ /*x*/}}','{{/*x*/ }}','{{-  /*x*/}}','{{/*x*/  -}}'):
            with self.subTest(comment=comment):
                with self.assertRaises(PromptRenderError):
                    render('{{if .ProxyAddr}}'+comment+'{{end}}',{'ProxyAddr':''},role='worker')
                values={key:'' for key in CATALOG['worker']}
                self.assertEqual(render_body('worker',values,override=comment),
                                 render_body('worker',values))

    def test_inactive_unsupported_directive_falls_back_to_complete_default(self):
        values={key:'' for key in CATALOG['worker']}
        invalid='prefix{{if .ProxyAddr}}{{printf "%s" .ProxyAddr}}{{end}}suffix'
        self.assertEqual(render_body('worker',values,override=invalid),render_body('worker',values))
