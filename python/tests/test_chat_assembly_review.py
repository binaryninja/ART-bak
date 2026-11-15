"""Independent opt-in assembly failure cleanup, without provider calls."""
import tempfile
import unittest
from unittest.mock import patch
from artex.server.chat_setup import build_chat_application
from artex.health import Registry
import test_chat_runtime as fixture
import test_chat_setup as setup_fixture
import test_application as application_fixture


class ChatAssemblyReview(unittest.TestCase):
    def test_failed_chat_construction_does_not_start_dispatcher(self):
        db = setup_fixture.ChatDB()
        with tempfile.TemporaryDirectory() as root:
            with patch('artex.server.chat_setup.ChatDispatcher') as dispatcher:
                with self.assertRaises((ValueError, TypeError)):
                    build_chat_application(db, b'x'*32, application_fixture.TestCodec(),
                        transport_factory=lambda _: (_ for _ in ()).throw(AssertionError('no provider')),
                        health=Registry(), transcript_root=root, authorize=lambda *_: True,
                        gate=fixture.ALLOW, tools=(object(),), agent_available=lambda _: True,
                        system_prompt=lambda _: 'fixture', single_process=True)
                dispatcher.assert_not_called()
