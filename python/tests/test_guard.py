import unittest
from artex.guard import Decision, Guard


class FakeInterceptor:
    def __init__(self, action='deny', *, enabled=True, matched=True, judged=True, approve=False):
        self.decision = Decision(action, 'Policy reason')
        self.enabled, self.matched, self.judged, self.approve = enabled, matched, judged, approve
        self.logs, self.asks, self.inputs = [], [], []

    def is_tool_enabled(self, tool):
        return self.enabled

    def match(self, tool, tool_input):
        self.inputs.append(tool_input)
        return self.decision, self.matched

    def judge(self, context, tool, tool_input):
        self.inputs.append(tool_input)
        return self.decision, self.judged

    def log(self, context, decision, tool, tool_input, status):
        self.logs.append(status)

    def handle_ask(self, context, decision, tool, tool_input):
        self.asks.append(tool_input)
        return self.approve


class GuardTests(unittest.TestCase):
    def test_passthrough_audit_only(self):
        g = Guard()
        # Strings only: no commands are executed by the guard or these tests.
        for cmd in ('ls -la', 'rm -rf /', 'curl http://example.com'):
            self.assertEqual(g.pre_tool_use('Bash', {'command': cmd}).decision, '')
        self.assertEqual(len(g.audit()), 3)
        self.assertEqual(g.audit()[-1].command, 'curl http://example.com')

    def test_command_extraction(self):
        g = Guard()
        g.pre_tool_use('shell_send', '{"text":"hello"}')
        g.pre_tool_use('shell_open', {'command': 'hello again'})
        g.pre_tool_use('other', {'command': 'not shell'})
        g.pre_tool_use('Bash', b'invalid')
        self.assertEqual([a.command for a in g.audit()], ['hello', 'hello again', '', ''])

    def test_deny_and_complete_input(self):
        ic = FakeInterceptor()
        data = {'command': 'hello', 'extra': {'important': True}}
        g = Guard(ic)
        r = g.pre_tool_use('Bash', data)
        self.assertEqual(r.decision, 'block')
        self.assertIn('not a target defense', r.message)
        self.assertEqual(ic.logs, ['denied'])
        self.assertIs(ic.inputs[0], data)
        self.assertEqual([a.action for a in g.audit()], ['allow', 'block'])

    def test_explicit_allow_and_unconfigured(self):
        ic = FakeInterceptor('allow')
        self.assertEqual(Guard(ic).pre_tool_use('Bash', {}).decision, '')
        self.assertEqual(ic.logs, ['allowed'])
        for ic in (FakeInterceptor(enabled=False), FakeInterceptor(matched=False, judged=False)):
            self.assertEqual(Guard(ic).pre_tool_use('Bash', {}).decision, '')

    def test_fallback_and_approval(self):
        self.assertEqual(Guard(FakeInterceptor(matched=False)).pre_tool_use('Bash', {}).decision, 'block')
        for approved in (False, True):
            ic = FakeInterceptor('ask', approve=approved)
            self.assertEqual(Guard(ic).pre_tool_use('Bash', {}).decision, '' if approved else 'block')
            self.assertEqual(len(ic.asks), 1)
        ic = FakeInterceptor('ask', approve=True)
        self.assertEqual(Guard(ic).pre_tool_use('Bash', {}, cancelled=True).decision, 'block')
        self.assertEqual(ic.asks, [])

    def test_attribution_and_snapshots(self):
        g = Guard()
        g.post_tool_use('Bash', '403 Forbidden', True)
        g.post_tool_use('Bash', b'failed', True)
        g.post_tool_use('Bash', 'success')
        g.post_tool_use('other', '403')
        self.assertEqual(g.attributions(), {'blocked': 1, 'error': 1, 'ok': 1})
        counts = g.attributions()
        counts.clear()
        self.assertEqual(len(g.attributions()), 3)
        for i in range(2005):
            g.pre_tool_use('Bash', {'command': str(i)})
        self.assertEqual(len(g.audit()), 2000)
        self.assertEqual(g.audit()[0].command, '5')
        rows = g.audit()
        rows.clear()
        self.assertEqual(len(g.audit()), 2000)


if __name__ == '__main__':
    unittest.main()
