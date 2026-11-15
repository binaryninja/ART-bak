import json
import unittest
from artex.agent.runtime import Action, BoundedAgent, Grant, Limits, RunStopped, Tool
from artex.manager import CancellationToken
from artex.providers import Config, Provider, Response, serialize


def assistant(text='Done', calls=()):
    blocks = ([{'type': 'text', 'text': text}] if text else [])
    blocks += [{'type': 'tool_use', 'id': key, 'name': name, 'input': args} for key, name, args in calls]
    return {'role': 'assistant', 'content': blocks}, 'tool_use' if calls else 'end_turn', {'input_tokens': 2, 'output_tokens': 2}


def request():
    return {'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Test inert tools'}]}]}


class FakeProvider:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
    def complete(self, context, req):
        # Exercise exactly the same serialization used by the concrete adapters.
        serialize(Config('openai', 'fake'), req)
        serialize(Config('anthropic', 'fake'), req)
        self.requests.append(req)
        return self.responses.pop(0)


def grant(context, action):
    return Grant(True, True, True)


def echo(context, arguments):
    context.check()
    return arguments.get('value', 'ok')


def tool(callback=echo):
    return Tool('echo', 'Inert test echo', {'type': 'object'}, callback, lambda args: isinstance(args.get('value', ''), str))


class RuntimeTests(unittest.TestCase):
    def test_tool_loop_exact_pairing_and_registered_schemas(self):
        provider = FakeProvider(assistant('', [('1', 'echo', {'value': 'result'})]), assistant())
        r = request(); r['tools'] = [{'name': 'unregistered'}]
        result = BoundedAgent(provider, gate=grant, tools=[tool()]).run(r)
        self.assertEqual(result.reason, 'completed')
        self.assertEqual((result.turns, result.tool_calls), (2, 1))
        self.assertEqual(result.usage['input_tokens'], 4)
        self.assertEqual(provider.requests[0]['tools'][0]['name'], 'echo')
        paired = provider.requests[1]['messages'][-1]['content'][0]
        self.assertEqual((paired['tool_use_id'], paired['content'][0]['text']), ('1', 'result'))
        self.assertFalse(paired['is_error'])
        self.assertEqual(len(r['messages']), 1)

    def test_missing_gate_never_calls_provider(self):
        p = FakeProvider()
        result = BoundedAgent(p).run(request())
        self.assertEqual(result.reason, 'permission_denied')
        self.assertFalse(p.requests)

    def test_each_grant_dimension_fails_closed(self):
        for decision in (Grant(False, True, True), Grant(True, False, True), Grant(True, True, False), True, None, Grant(1, True, True)):
            calls = []
            p = FakeProvider(assistant('', [('1', 'echo', {})]))
            def gate(ctx, action):
                return grant(ctx, action) if action.kind == 'model' else decision
            result = BoundedAgent(p, gate=gate, tools=[tool(lambda *args: calls.append(args))]).run(request())
            self.assertEqual(result.reason, 'permission_denied')
            self.assertFalse(calls)
            self.assertTrue(result.messages[-1]['content'][0]['is_error'])

    def test_cancelled_during_approval_never_executes(self):
        token = CancellationToken()
        calls = []
        def gate(ctx, action):
            if action.kind == 'tool': token.cancel()
            return grant(ctx, action)
        p = FakeProvider(assistant('', [('1', 'echo', {})]))
        result = BoundedAgent(p, gate=gate, tools=[tool(lambda *args: calls.append(args))]).run(request(), token=token)
        self.assertEqual(result.reason, 'cancelled')
        self.assertFalse(calls)

    def test_cancel_between_two_calls_skips_second_and_pairs(self):
        token = CancellationToken(); calls = []
        def callback(ctx, args):
            calls.append(args)
            token.cancel()
            return 'effect completed'
        p = FakeProvider(assistant('', [('1', 'echo', {}), ('2', 'echo', {})]))
        result = BoundedAgent(p, gate=grant, tools=[tool(callback)]).run(request(), token=token)
        self.assertEqual(result.reason, 'cancelled')
        self.assertEqual(len(calls), 1)
        results = result.messages[-1]['content']
        self.assertEqual([r['tool_use_id'] for r in results], ['1', '2'])
        self.assertFalse(results[0]['is_error'])
        self.assertTrue(results[1]['is_error'])
        serialize(Config('openai', 'validation'), {'messages': result.messages})

    def test_unknown_tool_or_bad_input_never_executes(self):
        for name, args, expected in [('missing', {}, 'unknown_tool'), ('echo', {'value': 2}, 'invalid_tool_input')]:
            calls = []
            p = FakeProvider(assistant('', [('1', name, args)]))
            result = BoundedAgent(p, gate=grant, tools=[tool(lambda *args: calls.append(args))]).run(request())
            self.assertEqual(result.reason, expected)
            self.assertFalse(calls)

    def test_gate_cannot_mutate_approved_arguments(self):
        observed = []
        def gate(ctx, action):
            args = action.arguments()
            args['value'] = 'changed'
            return grant(ctx, action)
        p = FakeProvider(assistant('', [('1', 'echo', {'value': 'original'})]), assistant())
        result = BoundedAgent(p, gate=gate, tools=[tool(lambda ctx, args: observed.append(args['value']) or 'ok')]).run(request())
        self.assertEqual(result.reason, 'completed')
        self.assertEqual(observed, ['original'])

    def test_turn_tool_and_token_budgets(self):
        p = FakeProvider(assistant('', [('1', 'echo', {})]))
        self.assertEqual(BoundedAgent(p, gate=grant, tools=[tool()], limits=Limits(max_turns=1)).run(request()).reason, 'max_turns')
        p = FakeProvider(assistant('', [('1', 'echo', {}), ('2', 'echo', {})]))
        r = BoundedAgent(p, gate=grant, tools=[tool()], limits=Limits(max_tool_calls=1)).run(request())
        self.assertEqual((r.reason, r.tool_calls), ('tool_budget', 1))
        p = FakeProvider(assistant('', [('1', 'echo', {})]))
        r = BoundedAgent(p, gate=grant, tools=[tool()], limits=Limits(max_tokens=4)).run(request())
        self.assertEqual((r.reason, r.tool_calls), ('token_budget', 0))
        self.assertEqual(p.requests[0]['max_tokens'], 4)

    def test_timeout_after_gate_and_output_cap(self):
        now = [0]
        def gate(ctx, action):
            if action.kind == 'tool': now[0] = 2
            return grant(ctx, action)
        p = FakeProvider(assistant('', [('1', 'echo', {})]))
        r = BoundedAgent(p, gate=gate, tools=[tool()], clock=lambda: now[0], limits=Limits(max_seconds=1)).run(request())
        self.assertEqual((r.reason, r.tool_calls), ('timeout', 0))
        p = FakeProvider(assistant('', [('1', 'echo', {'value': 'abcdefghijk'})]), assistant())
        r = BoundedAgent(p, gate=grant, tools=[tool()], limits=Limits(max_tool_output_bytes=4)).run(request())
        self.assertLessEqual(len(r.messages[2]['content'][0]['content'][0]['text'].encode()), 4)

    def test_refusal_and_truncation_are_not_retried(self):
        p = FakeProvider(assistant('I cannot help with that'))
        r = BoundedAgent(p, gate=grant).run(request())
        self.assertEqual(r.text, 'I cannot help with that')
        self.assertEqual(len(p.requests), 1)
        msg, _, usage = assistant('', [('1', 'echo', {})])
        for stop, reason in [('content_filter', 'refused'), ('max_tokens', 'output_limit')]:
            p = FakeProvider((msg, stop, usage))
            r = BoundedAgent(p, gate=grant, tools=[tool()]).run(request())
            self.assertEqual((r.reason, r.tool_calls), (reason, 0))

    def test_callback_failure_stops_without_retry(self):
        def callback(ctx, args): raise RuntimeError('private failure details')
        p = FakeProvider(assistant('', [('1', 'echo', {}), ('2', 'echo', {})]))
        r = BoundedAgent(p, gate=grant, tools=[tool(callback)]).run(request())
        self.assertEqual((r.reason, r.tool_calls), ('tool_error', 1))
        self.assertNotIn('private failure', json.dumps(r.messages))
        self.assertEqual(len(r.messages[-1]['content']), 2)

    def test_concrete_provider_roundtrip_with_inert_callback(self):
        wires = []
        responses = [
            {'choices': [{'message': {'tool_calls': [{'id': 'one', 'type': 'function', 'function': {'name': 'echo', 'arguments': '{"value":"ok"}'}}]}, 'finish_reason': 'tool_calls'}]},
            {'choices': [{'message': {'content': 'Finished'}, 'finish_reason': 'stop'}]},
        ]
        def transport(ctx, url, headers, body, stream):
            wires.append(json.loads(body))
            return Response(200, json.dumps(responses.pop(0)))
        r = BoundedAgent(Provider(Config('openai', 'mock'), transport), gate=grant, tools=[tool()]).run(request())
        self.assertEqual((r.reason, r.text), ('completed', 'Finished'))
        self.assertEqual(wires[1]['messages'][-1], {'role': 'tool', 'tool_call_id': 'one', 'content': 'ok'})

    def test_stop_reason_consistency_prevents_execution(self):
        for calls, stop, reason in [([('1', 'echo', {})], 'end_turn', 'invalid_protocol'),
                                    ([], 'tool_use', 'invalid_protocol'),
                                    ([('1', 'echo', {})], 'pause_turn', 'provider_paused')]:
            message, _, usage = assistant('Done', calls)
            p = FakeProvider((message, stop, usage))
            r = BoundedAgent(p, gate=grant, tools=[tool()]).run(request())
            self.assertEqual((r.reason, r.tool_calls), (reason, 0))

    def test_invalid_ids_and_nonfinite_limits(self):
        p = FakeProvider(assistant('', [('same', 'echo', {}), ('same', 'echo', {})]))
        r = BoundedAgent(p, gate=grant, tools=[tool()]).run(request())
        self.assertEqual((r.reason, r.tool_calls), ('invalid_protocol', 0))
        with self.assertRaises(ValueError): Limits(max_seconds=float('inf'))
        with self.assertRaises(ValueError): Limits(max_turns=0)
        p = FakeProvider()
        token = CancellationToken(); token.cancel()
        self.assertEqual(BoundedAgent(p, gate=grant).run(request(), token=token).reason, 'cancelled')
        self.assertFalse(p.requests)


if __name__ == '__main__': unittest.main()
