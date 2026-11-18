from contextlib import contextmanager
import json
import unittest

from artex.servicenow_recon import reconcile_preamble
from artex.server.servicenow_recon import DESCRIPTION, GOAL


class DB:
    def __init__(self):
        self.state = 'running'
        self.writes = []
        self.evidence = [('recon_manifest', json.dumps({'generation_id': 'g', 'session_id': 's'})),
                         ('recon_report', json.dumps({'generation_id': 'g', 'verified': False, 'observations': []}))]
        self.records = [('ok', 's', json.dumps({'stop_reason': 'end_turn', 'message': {'content': [{'type': 'text', 'text': 'I will inspect.'}]}}))]
    @contextmanager
    def transaction(self): yield self
    def fetch_one(self, q): return (9, DESCRIPTION, GOAL, self.state)
    def fetch_all(self, q):
        if 'FROM activity' in q.sql: return self.evidence
        if 'FROM llm_records' in q.sql: return self.records
        if 'completed_at' in q.sql: return [(2, self.state, False, False, None, '', 'timestamp', None, None, None, None)]
        raise AssertionError(q.sql)
    def execute(self, q):
        self.writes.append(q.sql)
        if q.sql.startswith('UPDATE tasks'): self.state = 'failed'
        return 1


class ReconciliationTests(unittest.TestCase):
    def test_exact_preamble_can_be_marked_failed_without_replay(self):
        db = DB()
        reconcile_preamble(db, 2)
        self.assertEqual(db.state, 'failed')
        self.assertEqual(len(db.writes), 2)

    def test_tool_effects_or_different_evidence_cannot_be_reconciled(self):
        changes = [
            lambda db: setattr(db, 'state', 'done'),
            lambda db: db.evidence.insert(1, ('recon_observation', '{}')),
            lambda db: db.records.append(db.records[0]),
            lambda db: setattr(db, 'records', [('ok', 'other', db.records[0][2])]),
            lambda db: setattr(db, 'records', [('ok', 's', json.dumps({'stop_reason': 'tool_use', 'message': {'content': [{'type': 'tool_use'}]}}))]),
        ]
        for change in changes:
            db = DB(); change(db)
            with self.subTest(change=change), self.assertRaises(RuntimeError): reconcile_preamble(db, 2)
            self.assertEqual(db.writes, [])


if __name__ == '__main__': unittest.main()
