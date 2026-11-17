"""Offline contract tests; all completions are deterministic local fakes."""
import copy
import unittest

from artex.sidequestion import (
    AnswerError, CancelledError, Capture, ContextBudgetError, Exchange, Memory, Model, Parent,
    Replay, SideQuestionService, Snapshot, build_request, estimate_input_tokens,
    input_budget, message_groups, output_budget, user_text, _ContextBuilder,
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

    def test_nonpositive_windows_use_default_for_request_and_summary_budgets(self):
        for window in (0, -1, -200000):
            with self.subTest(window=window):
                snapshot = self.snapshot(window=window)
                self.assertEqual(input_budget(snapshot, 8192), 183616)
                self.assertEqual(build_request(snapshot, [], 'q'),
                                 build_request(self.snapshot(window=200000), [], 'q'))
                fake = Fake()
                _, info = SideQuestionService(fake).respond(
                    self.snapshot('x' * 500000, window=window), 'q')
                self.assertEqual(info.input_budget, 183616)
                self.assertTrue(info.snapshot_summarized)
                self.assertGreater(len(fake.calls), 1)
                for request in fake.calls[:-1]:
                    self.assertLessEqual(estimate_input_tokens(request) +
                                         request['max_tokens'] + 512, 200000)

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

    def test_long_replay_fits_small_window_and_reuses_saved_summary(self):
        # Go context_test.go exercises 20 long replies under the shared call cap.
        # A slightly larger fixture exposes the Python fitter's half-chunk loss.
        history = [Exchange(f'question-{i}', 'evidence ' * 3300, ordinal=i)
                   for i in range(1, 21)]
        snapshot = self.snapshot(window=32000)
        before = copy.deepcopy(snapshot)
        saves, loads = [], []

        def load(after):
            loads.append(after)
            return [exchange for exchange in history if exchange.ordinal > after][:20]

        fake = Fake()
        out, info = SideQuestionService(fake).respond(
            snapshot, 'current status?', Replay(load=load, save=saves.append))
        summaries = [request for request in fake.calls if request.get('thinking') == 'disabled']
        self.assertGreater(len(summaries), 1)
        self.assertLessEqual(len(summaries), 12)
        for request in summaries:
            self.assertNotIn('tools', request)
            self.assertEqual(request['max_tokens'], 2048)
            self.assertLessEqual(estimate_input_tokens(request) + 2048 + 512, 32000)
        self.assertEqual(out.usage, {'input_tokens': len(summaries) + 1,
                                    'output_tokens': 2 * (len(summaries) + 1)})
        self.assertEqual(info.recent_exchanges, 0)
        self.assertTrue(info.history_summarized)
        self.assertLessEqual(info.estimated_input_tokens, info.input_budget)
        self.assertEqual(saves[-1].through, 20)
        self.assertEqual(snapshot, before)

        restarted = Fake()
        loads.clear()
        SideQuestionService(restarted).respond(
            snapshot, 'continue', Replay(memory=saves[-1], load=load, save=saves.append))
        self.assertEqual(loads, [20])
        self.assertEqual(len(restarted.calls), 1)

    def test_summary_chunks_preserve_multibyte_material_and_reserved_budget(self):
        for unit in ('x', '\u4e2d\U0001f600', '\\"\n'):
            for prior_length in range(12):
                with self.subTest(unit=unit, prior_length=prior_length):
                    fake = Fake()
                    builder = _ContextBuilder(SideQuestionService(fake), Replay(),
                                              self.snapshot(window=32000), None, None)
                    material = unit * 30000
                    summary = builder.summarize('p' * prior_length, material)
                    chunks = []
                    prior = 'p' * prior_length
                    for request in fake.calls:
                        self.assertLessEqual(estimate_input_tokens(request) +
                                             request['max_tokens'] + 512, 32000)
                        text = request['messages'][0]['content'][0]['text']
                        prefix = '[Previous summary]\n' + prior + '\n[New material]\n'
                        self.assertTrue(text.startswith(prefix))
                        chunks.append(text[len(prefix):])
                        self.assertTrue(chunks[-1])
                        chunks[-1].encode('utf-8', errors='strict')
                        prior = 'Retained evidence and unresolved questions.'
                    self.assertEqual(''.join(chunks), material)
                    self.assertEqual(summary, prior)
                    self.assertLessEqual(len(fake.calls), 12)

    def test_cancel_during_summary_retains_usage_without_save_or_answer(self):
        cancelled = [False]

        class CancellingFake(Fake):
            def complete(self, request):
                response = super().complete(request)
                cancelled[0] = True
                return response

        fake = CancellingFake()
        saves = []
        history = [Exchange('q', 'a', ordinal=i) for i in range(1, 22)]
        replay = Replay(load=lambda after: history if after == 0 else [], save=saves.append)
        with self.assertRaises(AnswerError) as error:
            SideQuestionService(fake).respond(self.snapshot(), 'q', replay,
                                              cancelled=lambda: cancelled[0])
        self.assertIsInstance(error.exception.__cause__, CancelledError)
        self.assertEqual(error.exception.answer.usage, {'input_tokens': 1, 'output_tokens': 2})
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]['thinking'], 'disabled')
        self.assertFalse(saves)
        self.assertEqual(replay.memory, Memory())

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
