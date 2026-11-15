"""Independent review: inert callbacks and disposable transcripts only."""
import unittest
from contextlib import nullcontext
from artex.agent.chat import ChatBinding, ChatBusy, ChatPersistenceError
from artex.task_chat import TaskChatCore, TaskChatScope
import test_chat_runtime as fixture
import test_task_chat as task_fixture


class TaskChatCoreReview(unittest.TestCase):
    def core(self, **overrides):
        self.live = True
        self.transcript = task_fixture.ScopedTranscript()
        self.provider = fixture.Provider([fixture.text('inert answer')])
        scope = TaskChatScope(1, 9, 0, 2, 'owned-revision', fixture.MODEL, 'fixture')
        options = dict(resolve=lambda *_: scope, current=lambda _: self.live,
            lifecycle_guard=lambda _: nullcontext(), transcript=self.transcript,
            provider_resolver=lambda _, model: ChatBinding(model, self.provider),
            gate=fixture.ALLOW)
        options.update(overrides)
        return TaskChatCore(**options)

    def test_capture_invalidates_scope_before_provider(self):
        core = self.core(capture=lambda _: setattr(self, 'live', False))
        with self.assertRaises(ChatPersistenceError):
            core.run(1, 'client', 'hello')
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.transcript.commits, [])
        self.assertTrue(core.running(1))
        self.live = True
        with self.assertRaises(ChatBusy): core.run(1, 'other', 'hello')

    def test_uncertain_begin_retains_task_without_provider(self):
        core = self.core()
        original = self.transcript.begin
        def uncertain(*args):
            original(*args)
            raise OSError('private fixture')
        self.transcript.begin = uncertain
        with self.assertRaises(ChatPersistenceError): core.run(1, 'client', 'hello')
        self.assertTrue(core.running(1))
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(len(self.transcript.turns), 1)

    def test_invalid_registry_before_durable_admission_releases_task(self):
        core = self.core(tools=(object(),))
        with self.assertRaises((ValueError, TypeError)): core.run(1, 'client', 'hello')
        self.assertFalse(core.running(1))
        self.assertEqual(self.transcript.history, {})
        self.assertEqual(self.provider.calls, [])
