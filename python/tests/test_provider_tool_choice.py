import json
import unittest
from artex.providers import Config, ProtocolError, serialize
from artex.llmrec import Recorder


class ToolChoiceTests(unittest.TestCase):
    def test_explicit_choice_for_supported_formats(self):
        for choice, anthropic in [('auto', 'auto'), ('required', 'any'), ('none', 'none')]:
            request = {'messages': [], 'tools': [{'name': 'inspect', 'input_schema': {'type': 'object'}}], 'tool_choice': choice}
            self.assertEqual(serialize(Config('openai', 'model'), request)['tool_choice'], choice)
            self.assertEqual(serialize(Config('anthropic', 'model'), request)['tool_choice'], {'type': anthropic})
            self.assertEqual(json.loads(Recorder(None).serialize_request(request))['tool_choice'], choice)

    def test_invalid_or_unregistered_choice_is_rejected(self):
        for choice in ['other', {}, ['required'], 1]:
            with self.subTest(choice=choice), self.assertRaises(ProtocolError):
                serialize(Config('openai', 'model'), {'tools': [{'name': 'inspect'}], 'tool_choice': choice})
        with self.assertRaises(ProtocolError):
            serialize(Config('openai', 'model'), {'tool_choice': 'required'})


if __name__ == '__main__': unittest.main()
