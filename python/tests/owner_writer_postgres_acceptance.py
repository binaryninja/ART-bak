"""Explicit opt-in fixture acceptance; never collected by normal test_ discovery.

Run with the existing locked interpreter, --config /absolute/root/config and
--sha256 the separately attested hash. No default path, DSN or environment input.
These tests exercise resource contracts, not production lifecycle authority.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from artex.db.agents import AgentStore
from artex.db.profiles import ProfileStore, ProfileNotFound
from artex.db.core import Query
from owner_writer_postgres_support import (
    FixtureRefused, Participant, acquire_lock, connect_fixture, load_fixture,
    observe_wait, prepare_schema, read_state, reconcile_uncertain, reset_rows)

_CONFIG = None
EVIDENCE = []


class PostgresAcceptance(unittest.TestCase):
    def setUp(self):
        if _CONFIG is None:
            self.skipTest('Explicit attested fixture configuration required')
        self.session = connect_fixture(_CONFIG)
        self.addCleanup(self.session.close)
        prepare_schema(self.session)
        reset_rows(self.session)

    def test_original_queries_commit_exact_rows_and_null_semantics(self):
        with self.session.db.transaction():
            acquire_lock(self.session)
            ProfileStore(self.session.db).set_active(2)
            AgentStore(self.session.db).update_metadata('analyst', '  Named  ', '')
        rows = read_state(self.session)
        self.assertEqual(rows['profiles'], [(1, False), (2, True)])
        self.assertEqual(rows['agents'][0], ('analyst', 'Named', None, False))
        self.assertEqual([q['rowcount'] for q in self.session.queries], [1, 1, 1])
        self.assertEqual(self.session.queries[-1]['params'], ['analyst', 'Named', ''])
        # Direct store calls are intentionally not fixture revision participants.
        self.assertEqual(rows['revision'], (0, False))
        EVIDENCE.append(dict(test=self.id(), state=rows, queries=self.session.queries))

    def test_nested_original_profile_rollback_preserves_outer_metadata(self):
        with self.session.db.transaction():
            acquire_lock(self.session)
            AgentStore(self.session.db).update_metadata('analyst', 'Outer', 'retained')
            with self.assertRaises(ProfileNotFound):
                ProfileStore(self.session.db).set_active(999)
        state = read_state(self.session)
        self.assertEqual(state['profiles'], [(1, True), (2, False)])
        self.assertEqual(state['agents'][0], ('analyst', 'Outer', 'retained', False))
        self.assertEqual([q['rowcount'] for q in self.session.queries], [1, 1, 0])
        EVIDENCE.append(dict(test=self.id(), state=state, queries=self.session.queries))

    def test_cross_process_wait_precedes_original_mutation(self):
        holder = Participant(_CONFIG.path, _CONFIG.sha256, 'hold')
        writer = None
        try:
            holder.wait('connected'); holder.wait('held')
            writer = Participant(_CONFIG.path, _CONFIG.sha256, 'profile', expected_revision=0)
            writer.wait('connected'); writer.send('go'); writer.wait('attempting')
            wait = observe_wait(self.session, writer)
            self.assertEqual(read_state(self.session)['profiles'], [(1, True), (2, False)])
            self.assertNotEqual(holder.identity['python_pid'], writer.identity['python_pid'])
            self.assertNotEqual(holder.identity['pid'], writer.identity['pid'])
            self.assertNotEqual(writer.identity['python_pid'], os.getpid())
            holder.send('commit'); self.assertEqual(holder.wait('settled')['outcome'], 'commit')
            settled = writer.wait('settled')
            self.assertEqual(settled['outcome'], 'commit')
            self.assertEqual(len(settled['queries']), 2)
            self.assertEqual(read_state(self.session)['revision'], (1, False))
            EVIDENCE.append(dict(test=self.id(), holder=holder.identity, writer=writer.identity,
                positive_wait=wait, settled=settled, state=read_state(self.session)))
        finally:
            errors = []
            # Release holder before joining waiter; attempt every cleanup even on failure.
            for child in (holder, writer):
                if child is not None:
                    try: child.close(timeout=10)
                    except BaseException as error: errors.append(type(error).__name__)
            if errors:
                self.fail('Unresolved/unsuccessful participant cleanup: ' + ','.join(errors))

    def test_stale_revision_refuses_before_original_query(self):
        writer = Participant(_CONFIG.path, _CONFIG.sha256, 'metadata', expected_revision=4)
        try:
            writer.wait('connected'); writer.send('go')
            settled = writer.wait('settled')
            self.assertEqual(settled['error']['type'], 'FixtureStale')
            self.assertEqual(settled['queries'], [])
            self.assertEqual(settled['phase'], 'locked')
            self.assertEqual(read_state(self.session)['revision'], (0, False))
            EVIDENCE.append(dict(test=self.id(), settled=settled))
        finally:
            writer.close(timeout=10)

    def test_explicit_uncertainty_poison_is_separate_committed_state(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic body'):
            with self.session.db.transaction():
                acquire_lock(self.session)
                AgentStore(self.session.db).update_metadata('analyst', 'Rolled back', '')
                self.session.db.execute(Query('UPDATE fixture_state SET revision=revision+1 WHERE id=1'))
                raise RuntimeError('synthetic body')
        state = read_state(self.session)
        self.assertEqual(state['revision'], (0, False))
        self.assertEqual(state['agents'][0][1], 'Initial')
        reconcile_uncertain(self.session, 0)
        self.assertEqual(read_state(self.session)['revision'], (1, True))
        EVIDENCE.append(dict(test=self.id(), known_rollback=state, explicit_poison=read_state(self.session)))

    def test_pre_go_close_performs_no_store_mutation(self):
        writer = Participant(_CONFIG.path, _CONFIG.sha256, 'metadata', expected_revision=0)
        writer.wait('connected')
        writer.close(timeout=10)
        self.assertEqual(read_state(self.session)['agents'][0][1], 'Initial')
        self.assertEqual(read_state(self.session)['revision'], (0, False))

    def test_hash_and_ambient_routing_gate(self):
        with self.assertRaises(FixtureRefused):
            load_fixture(_CONFIG.path, '0' * 64)
        # Synthetic name/value only; no ambient credential values inspected.
        key = 'PGOWNER_FIXTURE_NEGATIVE_CONTROL'
        self.assertNotIn(key, os.environ)
        os.environ[key] = 'synthetic'
        try:
            with self.assertRaises(FixtureRefused):
                connect_fixture(_CONFIG)
        finally:
            del os.environ[key]


def main():
    global _CONFIG
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--evidence', required=True)
    args = parser.parse_args()
    _CONFIG = load_fixture(args.config, args.sha256)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PostgresAcceptance)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    Path(args.evidence).write_text(json.dumps({'tests_run': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors),
        'observations': EVIDENCE, 'production_authority_claim': False}, indent=2) + '\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
