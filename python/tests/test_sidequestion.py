"""Offline contract tests; all completions are deterministic local fakes."""
import copy
import unittest

from artex.sidequestion import (
    AnswerError, Capture, ContextBudgetError, Exchange, Memory, Model, Parent,
    Replay, SideQuestionService, Snapshot, build_request, estimate_input_tokens,
    input_budget, message_groups, output_budget, user_text,
)


def result(text, stop='end_turn', usage=None):
    return user_text(text), stop, usage or {'input_tokens': 1, 'output_tokens': 2}


class Fake:
    def __init__(self, events=None):
        self.calls = []
        self.events = events or []
        self.fail_once = False
        self.bad_summary = False

    def complete(self, request):
        self.calls.append(copy.deepcopy(request))
        if request.get('thinking') == 'disabled':
            return result('Retained evidence and unresolved questions.', 'length' if self.bad_summary else 'end_turn')
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError('context_length_exceeded')
        return result('Local fake answer.')

    def stream(self, request):
        self.calls.append(copy.deepcopy(request))
        yield from self.events


class SideQuestionTests(unittest.TestCase):
    def snapshot(self, text='Main evidence', window=200000):
        return Snapshot(model=Model(window_tokens=window), request={
            'system': ['System'], 'messages': [user_text(text)], 'max_tokens': 32768,
            'tools': [{'name': 'not_executable'}], 'tool_choice': 'auto'})

    def test_parent_keys(self):
        self.assertEqual(Parent(conversation_id=7, task_id=4).key(), 'conv-7')
        self.assertEqual(Parent(task_id=4, exploration_id=2).key(), 'task-4-exp-2-main')
        self.assertEqual(Parent(task_id=4, intent_id=9).key(), 'task-4-exp-0-worker-i9')

    def test_capture_isolation_and_finish(self):
        published = []
        capture = Capture(Parent(), published.append, run_id=1)
        request = {'messages': [user_text('<system-reminder>host'), user_text('original')]}
        capture.save(request, Model())
        request['messages'].clear()
        published[0].request['messages'].clear()
        self.assertEqual(len(capture.last.request['messages']), 2)
        snapshot = capture.finish([user_text('terminal')])
        self.assertEqual(snapshot.version, 2)
        self.assertEqual(len(snapshot.request['messages']), 2)
        self.assertIsNone(capture.save({'bad': object()}, Model()))

    def test_request_limits_history_filters_and_tools(self):
        snapshot = self.snapshot()
        before = copy.deepcopy(snapshot)
        history = [Exchange(str(i), 'answer', ordinal=i) for i in range(22)]
        history.append(Exchange('failed', status='failed'))
        request = build_request(snapshot, history, ' question ')
        self.assertEqual(len(request['messages']), 42)
        self.assertEqual(request['max_tokens'], 8192)
        self.assertNotIn('tools', request)
        self.assertNotIn('tool_choice', request)
        self.assertEqual(snapshot, before)
        self.assertEqual(output_budget({'max_tokens': 512}, 99999), 512)
        self.assertEqual(input_budget(self.snapshot(window=10000), 1000), 8500)

    def test_budget_rejection(self):
        with self.assertRaises(ContextBudgetError):
            build_request(self.snapshot('x' * 100000, window=10000), [], 'question')

    def test_tool_pair_groups(self):
        messages = [user_text('first'), {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'a'}]},
                    user_text('interleaved'), {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a', 'content': 'result'}]}]
        self.assertEqual([len(g) for g in message_groups(messages)], [1, 3])
        self.assertEqual([len(g) for g in message_groups(messages[:-1])], [1, 2])

    def test_sync_and_stream_answers(self):
        fake = Fake([{'type': 'message_start', 'usage': {'input_tokens': 4}},
                     {'type': 'text_delta', 'text': 'Hi'}, {'type': 'message_stop'}])
        service = SideQuestionService(fake)
        self.assertEqual(service.answer({'tools': [{}]}).text, 'Local fake answer.')
        updates = []
        out = service.answer({}, True, updates.append)
        self.assertEqual(out.text, 'Hi')
        self.assertEqual(out.usage, {'input_tokens': 4})
        self.assertEqual(updates[0].text, '')
        self.assertNotIn('tools', fake.calls[0])

    def test_incomplete_stream_retains_partial_output(self):
        with self.assertRaises(AnswerError) as ctx:
            SideQuestionService(Fake([{'type': 'text_delta', 'text': 'partial'}])).answer({}, True)
        self.assertEqual(ctx.exception.answer.text, 'partial')

    def test_tool_only_fallback(self):
        out = SideQuestionService(Fake([{'type': 'tool_use_start'}, {'type': 'message_stop'}])).answer({}, True)
        self.assertTrue(out.tool_use)
        self.assertIn('cannot run tools', out.text)

    def test_replay_folds_and_persists_ordinal(self):
        history = [Exchange(str(i), 'a', ordinal=i) for i in range(1, 51)]
        saves = []
        replay = Replay(load=lambda after: [e for e in history if e.ordinal > after][:20], save=saves.append)
        out, info = SideQuestionService(Fake()).respond(self.snapshot(), 'q', replay)
        self.assertEqual(info.recent_exchanges, 20)
        self.assertTrue(info.history_summarized)
        self.assertEqual(saves[-1].through, 30)
        self.assertEqual(out.usage['input_tokens'], 3)
        self.assertEqual(replay.memory.through, 0)

    def test_invalid_cursor_and_generation_rejection(self):
        with self.assertRaisesRegex(AnswerError, 'cursor'):
            SideQuestionService(Fake()).respond(self.snapshot(), 'q', Replay(load=lambda after: [Exchange(ordinal=after)]))
        history = [Exchange(ordinal=i) for i in range(1, 22)]
        def reject(memory):
            raise RuntimeError('generation changed')
        with self.assertRaisesRegex(AnswerError, 'generation changed'):
            SideQuestionService(Fake()).respond(self.snapshot(), 'q', Replay(load=lambda after: history if not after else [], save=reject))

    def test_snapshot_compaction_cache_and_no_mutation(self):
        snapshot = self.snapshot('x' * 100000, window=24000)
        before = copy.deepcopy(snapshot)
        saves = []
        fake = Fake()
        _, info = SideQuestionService(fake).respond(snapshot, 'q', Replay(save=saves.append), output_tokens=1024)
        self.assertTrue(info.snapshot_summarized)
        self.assertEqual(snapshot, before)
        first_calls = len(fake.calls)
        self.assertGreater(first_calls, 1)
        fake2 = Fake()
        SideQuestionService(fake2).respond(snapshot, 'q', Replay(memory=saves[-1]), output_tokens=1024)
        self.assertEqual(len(fake2.calls), 1)

    def test_summary_rejects_truncation_without_save(self):
        fake = Fake()
        fake.bad_summary = True
        saves = []
        with self.assertRaisesRegex(AnswerError, 'not completed'):
            SideQuestionService(fake).respond(self.snapshot('x' * 100000, window=24000), 'q', Replay(save=saves.append), output_tokens=1024)
        self.assertFalse(saves)

    def test_overflow_one_retry_with_smaller_request(self):
        fake = Fake()
        fake.fail_once = True
        out, info = SideQuestionService(fake).respond(self.snapshot('x' * 30000), 'q')
        self.assertTrue(info.overflow_retried)
        answer_calls = [r for r in fake.calls if r.get('thinking') != 'disabled']
        self.assertEqual(len(answer_calls), 2)
        self.assertLess(estimate_input_tokens(answer_calls[-1]), estimate_input_tokens(answer_calls[0]))
        self.assertTrue(out.text)

    def test_cancelled_before_provider(self):
        fake = Fake()
        with self.assertRaisesRegex(AnswerError, 'cancelled'):
            SideQuestionService(fake).respond(self.snapshot(), 'q', cancelled=lambda: True)
        self.assertFalse(fake.calls)

    def test_summary_call_cap(self):
        fake = Fake()
        with self.assertRaisesRegex(AnswerError, 'call limit'):
            SideQuestionService(fake).respond(self.snapshot('x' * 1000000, window=24000), 'q', output_tokens=1024)
        self.assertEqual(len(fake.calls), 12)


if __name__ == '__main__':
    unittest.main()
