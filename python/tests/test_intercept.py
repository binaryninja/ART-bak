import json
import threading
import unittest
from artex.intercept import (AlreadyDecided, Context, Decision, Interceptor,
    ReviewBackground, Rule, bounded, build_review_input, digest_input, parse_verdict)
from artex.guard import Guard


class MemoryStore:
    def __init__(self, rules=(), tools=None):
        self.rules, self.tools, self.rows = list(rules), tools, {}
        self.lock = threading.RLock()
        self.early = None
    def list_rules(self): return self.rules
    def get_enabled_tools(self): return self.tools
    def create_pending(self, *args):
        with self.lock:
            key = len(self.rows)+1
            self.rows[key] = {'status': 'pending'}
            if self.early:
                self.early(key)
            return key
    def get_pending(self, key):
        with self.lock: return dict(self.rows[key])
    def resolve_pending(self, key, status, action, reason):
        with self.lock:
            if self.rows.get(key, {}).get('status') != 'pending': return False
            self.rows[key] = {'status': status, 'effective_action': action}
            return True
    def log_decision(self, *args): pass


class InterceptTests(unittest.TestCase):
    def test_priority_disabled_and_match_target(self):
        rules = [Rule(id=1, pattern='Bash', action='allow'),
                 Rule(id=2, priority=9, pattern='marker', match_target='tool_input', action='deny'),
                 Rule(priority=20, enabled=False, action='deny')]
        ic = Interceptor(MemoryStore(rules))
        self.assertEqual(ic.match('Bash', {'command': 'marker'})[0].rule_id, 2)
        self.assertEqual(ic.match('Bash', '{}')[0].rule_id, 1)
        self.assertFalse(ic.match('Read', '{}')[1])

    def test_regex_and_cache_invalidation(self):
        store = MemoryStore([Rule(pattern='^Bash$', match_type='regex', action='deny')])
        ic = Interceptor(store)
        self.assertTrue(ic.match('Bash', '{}')[1])
        self.assertFalse(ic.match('MyBash', '{}')[1])
        store.rules = []
        self.assertTrue(ic.match('Bash', '{}')[1])
        ic.invalidate()
        self.assertFalse(ic.match('Bash', '{}')[1])

    def test_configuration_error_blocks_not_bypasses(self):
        for store in [MemoryStore([Rule(pattern='[', match_type='regex')]), MemoryStore(tools='bad')]:
            ic = Interceptor(store)
            self.assertTrue(ic.is_tool_enabled('anything'))
            self.assertEqual(Guard(ic).pre_tool_use('anything', {}).decision, 'block')

    def test_enabled_default_and_explicit_empty(self):
        self.assertTrue(Interceptor(MemoryStore()).is_tool_enabled('Bash'))
        self.assertFalse(Interceptor(MemoryStore(tools=[])).is_tool_enabled('Bash'))

    def test_context_only_human_background(self):
        ctx = Context('/local', ReviewBackground('worker', 'untrusted summary'))
        self.assertNotIn('background', build_review_input(ctx, 'Read', '{}'))
        ctx.background = ReviewBackground('user_message', 'a'*4001)
        result = build_review_input(ctx, 'Read', '{"path":"x"}')
        self.assertTrue(result['background']['truncated'])
        self.assertEqual(len(result['background']['text']), 4000)
        self.assertEqual(result['arguments'], {'path':'x'})
        self.assertEqual(set(result), {'version', 'tool_name', 'arguments', 'working_directory', 'background'})

    def test_invalid_json_is_not_reviewed(self):
        calls = []
        ic = Interceptor(MemoryStore(), lambda *args: calls.append(args))
        for value in ['{', 'NaN', '{} trailing']:
            self.assertEqual(ic.judge(None, 'Bash', value)[0].action, 'ask')
        self.assertFalse(calls)

    def test_judge_failure_and_invalid_action(self):
        ic = Interceptor(MemoryStore(), lambda *a: Decision('bogus'))
        result, judged = ic.judge(None, 'Bash', '{}')
        self.assertTrue(judged)
        self.assertEqual(result.action, 'deny')
        self.assertTrue(result.model_fallback)
        self.assertTrue(result.model_input_digest)
        self.assertFalse(Interceptor(MemoryStore()).judge(None, 'Bash', '{}')[1])

    def test_judge_strips_rule_identity(self):
        ic = Interceptor(MemoryStore(), lambda *a: Decision('ask', rule_id=5))
        decision, _ = ic.judge(None, 'Bash', '{}')
        self.assertEqual(decision.rule_id, 0)
        self.assertTrue(decision.timeout_enabled)
        self.assertEqual(decision.timeout_action, 'deny')

    def test_verdict_parser_strict(self):
        valid = '{"decision":"deny","comment":"Operation: read; Consequence: data; Rule: A5"}'
        self.assertEqual(parse_verdict(valid).action, 'deny')
        self.assertEqual(parse_verdict('```json\n'+valid+'\n```').action, 'deny')
        invalid = [valid+' trailing', 'prefix '+valid, valid[:-1],
                   valid.replace('"decision":"deny"', '"decision":"deny","decision":"allow"'),
                   valid.replace('Rule: A5', 'Rule: '), valid.replace('"deny"', 'null'),
                   valid.replace('"deny"', '"other"')]
        for value in invalid:
            self.assertEqual(parse_verdict(value).action, '', value)

    def test_utf8_boundaries_and_digest(self):
        self.assertEqual(bounded('a\N{EURO SIGN}b', 3), ('a', True))
        self.assertEqual(digest_input('{"b":2, "a":1}'), digest_input('{"a":1,"b":2}'))

    def test_approval_before_registration(self):
        store = MemoryStore()
        ic = Interceptor(store)
        store.early = lambda key: ic.decide(key, True)
        self.assertTrue(ic.handle_ask(None, Decision('ask'), 'Bash', '{}'))
        self.assertFalse(ic._pending)
        with self.assertRaises(AlreadyDecided): ic.decide(1, False)

    def test_approval_after_registration(self):
        store = MemoryStore()
        ic = Interceptor(store)
        ctx = Context(emit=lambda activity: ic.decide(activity['pending_id'], True))
        self.assertTrue(ic.handle_ask(ctx, Decision('ask'), 'Bash', '{}'))

    def test_cancelled_request_denied(self):
        event = threading.Event()
        ctx = Context(cancelled=event, emit=lambda activity: event.set())
        store = MemoryStore()
        self.assertFalse(Interceptor(store).handle_ask(ctx, Decision('ask'), 'Bash', '{}'))
        self.assertEqual(store.rows[1]['effective_action'], 'deny')

    def test_timeout_defaults_deny_and_explicit_allow(self):
        for action in ('deny', 'allow', 'unknown'):
            store = MemoryStore()
            ic = Interceptor(store)
            self.assertEqual(ic.handle_ask(None, Decision('ask', timeout_enabled=True, timeout_seconds=.001,
                timeout_action=action), 'Bash', '{}'), action == 'allow')
            self.assertEqual(store.rows[1]['status'], 'timeout')

    def test_storage_failure_denies(self):
        store = MemoryStore()
        def fail(*args): raise RuntimeError('offline')
        store.create_pending = fail
        self.assertFalse(Interceptor(store).handle_ask(None, Decision('ask'), 'Bash', '{}'))


if __name__ == '__main__': unittest.main()
