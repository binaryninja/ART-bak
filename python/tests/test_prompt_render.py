import unittest
from artex.agent.prompt_render import render,render_body,builtin_templates,PromptRenderError,RenderLimits,CATALOG

class PromptRenderTests(unittest.TestCase):
    def test_source_worker_conditional(self):
        tmpl='{{if .ProxyAddr}}Use proxy {{.ProxyAddr}}{{else}}Manual{{end}}'
        self.assertEqual(render(tmpl,{'ProxyAddr':'127.0.0.1:8080'},role='worker'),'Use proxy 127.0.0.1:8080')
        self.assertEqual(render(tmpl,{'ProxyAddr':''},role='worker'),'Manual')
    def test_nested_if_and_truthiness(self):
        tmpl='{{if .ProxyAddr}}A{{if .WorkerName}}{{.WorkerName}}{{else}}none{{end}}{{else}}B{{end}}'
        self.assertEqual(render(tmpl,{'ProxyAddr':'false','WorkerName':'worker'},role='worker'),'Aworker')
    def test_verbatim_not_html_or_recursive(self):
        value='<a>& {{.Now}}'
        self.assertEqual(render('{{.Goal}}',{'Goal':value},role='mainagent'),value)
    def test_source_whitespace_trim(self):
        tmpl='one \t\n{{- .Goal -}}\r\n two'
        self.assertEqual(render(tmpl,{'Goal':'G'},role='mainagent'),'oneGtwo')
        self.assertEqual(render('one\u00a0{{- .Goal}}',{'Goal':'G'},role='mainagent'),'one\u00a0G')
        with self.assertRaises(PromptRenderError):render('{{-.Goal}}',{'Goal':'G'},role='mainagent')
    def test_comment_with_delimiters_and_trim(self):
        self.assertEqual(render('a {{- /* {{.Bogus}} ignored */ -}} b',{},role='worker'),'ab')
    def test_strict_missing_unknown_and_inactive_arm(self):
        for template,variables in (('{{.Now}}',{}),('{{.Bogus}}',{}),('{{if .ProxyAddr}}{{.WorkerName}}{{end}}',{'ProxyAddr':''})):
            with self.assertRaises(PromptRenderError):render(template,variables,role='worker')
    def test_no_functions_methods_pipelines_or_execution(self):
        for template in ('{{printf "%s" .Goal}}','{{.Goal | html}}','{{range .Goal}}x{{end}}',
                         '{{.Goal.Method}}','{{with .Goal}}x{{end}}','{{$x := .Goal}}','{{template "x"}}',
                         '{{if .Goal}}x{{else if .Goal}}y{{end}}'):
            with self.assertRaises(PromptRenderError):render(template,{'Goal':'x'},role='mainagent')
    def test_unbalanced_syntax(self):
        for template in ('{{','{{if .Goal}}','{{else}}','{{end}}','{{if .Goal}}{{else}}{{else}}{{end}}','{{/*oops'):
            with self.assertRaises(PromptRenderError):render(template,{'Goal':'x'},role='mainagent')
    def test_all_bounds(self):
        cases=[('long',{},RenderLimits(max_template_bytes=1)),('{{.Goal}}',{'Goal':'éé'},RenderLimits(max_output_bytes=3)),
               ('{{.Goal}}{{.Goal}}',{'Goal':'x'},RenderLimits(max_actions=1)),
               ('{{if .Goal}}{{if .Goal}}x{{end}}{{end}}',{'Goal':'x'},RenderLimits(max_depth=1))]
        for template,variables,limits in cases:
            with self.assertRaises(PromptRenderError):render(template,variables,role='mainagent',limits=limits)
    def test_seed_mapping_and_default_fallback(self):
        seeds=builtin_templates();self.assertEqual(set(seeds),{'mainagent','worker','planner','goals','auto','pentest'})
        for role in seeds:
            variables={k:'fixture' for k in CATALOG[role]}
            result=render_body(role,variables)
            self.assertTrue(result);self.assertNotIn('{{.',result)
            self.assertEqual(render_body(role,variables,override='{{.Unknown}}'),result)
        seeds['worker']='modified'
        self.assertNotEqual(builtin_templates()['worker'],'modified')
    def test_unsupported_role_no_guessed_seed(self):
        for role in ('reporter','custom','unknown'):
            with self.assertRaises(PromptRenderError):render_body(role,{})
        self.assertEqual(render('{{.Now}}',{'Now':'explicit time'},role='custom'),'explicit time')
    def test_no_raw_default_when_variables_missing(self):
        with self.assertRaises(PromptRenderError):render_body('mainagent',{},override='{{.Unknown}}')
    def test_bad_values_and_limits(self):
        for value in (3,'\x00','\ud800'):
            with self.assertRaises(PromptRenderError):render('{{.Goal}}',{'Goal':value},role='mainagent')
        with self.assertRaises(ValueError):RenderLimits(max_depth=True)

if __name__=='__main__':unittest.main()
