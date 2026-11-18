"""Offline negative checks for the live acceptance evidence verifier."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'integration'
sys.path.insert(0, str(ROOT))
try:
    spec = importlib.util.spec_from_file_location('provider_acceptance_target', ROOT / 'provider_fixture_acceptance.py')
    target = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)
finally:
    sys.path.remove(str(ROOT))


def exchange():
    call = {'role': 'assistant', 'content': [dict(type='tool_use', id='call-1', name=target.TOOL, input={})]}
    result = dict(type='tool_result', tool_use_id='call-1', content=[dict(type='text', text='fixture-value')], is_error=False)
    first = dict(status='ok', raw_request=None, raw_response=None,
                 request=dict(tools=[target.TOOL], messages=[]),
                 response=dict(message=call, stop_reason='tool_use'))
    second = dict(status='ok', raw_request=None, raw_response=None,
                  request=dict(tools=[target.TOOL], messages=[call, dict(role='user', content=[result])]),
                  response=dict(message=dict(role='assistant', content=[dict(type='text', text='fixture-value')]), stop_reason='end_turn'))
    return [first, second]


class EvidenceTests(unittest.TestCase):
    def test_accepts_exact_linked_exchange(self):
        self.assertEqual(target.verify_exchange(exchange(), 'fixture-value'), 'call-1')

    def test_rejects_forged_or_unlinked_evidence(self):
        original = exchange()
        mutations = [
            lambda r: r.pop(),
            lambda r: r[0]['request'].update(system='fixture-value'),
            lambda r: r[0]['request'].update(tools=[target.TOOL, 'shell']),
            lambda r: r[1].update(status='error'),
            lambda r: r[0].update(raw_request='headers'),
            lambda r: r[0]['response']['message']['content'][0].update(input={'unexpected': True}),
            lambda r: r[1]['request']['messages'][-1]['content'][0].update(tool_use_id='other'),
            lambda r: r[1]['request']['messages'][-1]['content'][0].update(is_error=True),
            lambda r: r[1]['request']['messages'][-1]['content'][0].update(content=[dict(type='text', text='wrong')]),
            lambda r: r[1]['response']['message']['content'][0].update(text='wrong'),
        ]
        for mutation in mutations:
            rows = deepcopy(original)
            mutation(rows)
            with self.subTest(mutation=mutation), self.assertRaises(target.AcceptanceFailure):
                target.verify_exchange(rows, 'fixture-value')


if __name__ == '__main__':
    unittest.main()
