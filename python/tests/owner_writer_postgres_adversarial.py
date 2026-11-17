"""Explicit opt-in PostgreSQL participating-writer counterexamples.

Only the separately attested disposable fixture may be used. No connection is
opened on import or unittest discovery. This tests a proposed cooperative route
and original store SQL, not genuine ART owner issuance or task readiness.
Run this file with --config, --sha256 and an external --evidence path in a
process whose environment contains no PostgreSQL routing variables.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import unittest

from artex.db.core import Query
from owner_writer_postgres_support import (
    Participant, advisory_locks, cancel_waiter, connect_fixture, load_fixture,
    observe_backend, observe_wait, prepare_schema, read_state, reset_rows,
)


PROFILE_SQL = (
    'UPDATE llm_profiles SET is_default=false WHERE is_default',
    'UPDATE llm_profiles SET is_default=true WHERE id=$1',
)
METADATA_SQL = "UPDATE agents SET name=$2, description=NULLIF($3,'') WHERE key=$1 AND builtin=false"


class OwnerWriterPostgresAdversarialTests(unittest.TestCase):
    fixture = None
    evidence = []

    def setUp(self):
        if self.fixture is None:
            self.skipTest('Explicit attested fixture CLI is required')
        self.record = {'test': self.id(), 'observations': [], 'participants': [], 'cleanup': []}
        self.evidence.append(self.record)
        self.children = []
        self.observer = connect_fixture(self.fixture)
        self.addCleanup(self.observer.close)
        self.addCleanup(self.cleanup_children)
        self.record['observer'] = self.observer.identity
        prepare_schema(self.observer)
        reset_rows(self.observer)
        self.initial = read_state(self.observer)
        self.record['initial'] = self.initial
        self.assertEqual(self.initial['profiles'], [(1, True), (2, False)])
        self.assertEqual(self.initial['revision'], (0, False))

    def child(self, operation, **arguments):
        participant = Participant(self.fixture.path, self.fixture.sha256, operation, **arguments)
        entry = {'participant': participant, 'operation': operation, 'started': operation == 'hold', 'settled': False}
        self.children.append(entry)
        connected = participant.wait('connected', timeout=5)
        identity = connected['identity']
        self.assertEqual(identity['python_pid'], participant.process.pid)
        self.assertNotEqual(identity['python_pid'], os.getpid())
        self.assertNotEqual(identity['pid'], self.observer.identity['pid'])
        earlier = [item['participant'].identity for item in self.children[:-1]]
        self.assertNotIn(identity['python_pid'], [item['python_pid'] for item in earlier])
        self.assertNotIn(identity['pid'], [item['pid'] for item in earlier])
        self.assertEqual(identity['schema'], self.observer.identity['schema'])
        self.assertEqual(identity['fixture_id'], self.observer.identity['fixture_id'])
        if operation == 'hold':
            participant.wait('held', timeout=5)
            self.assertEqual(advisory_locks(self.observer, participant), [(True, 'ExclusiveLock')])
        return participant

    def entry(self, participant):
        return next(item for item in self.children if item['participant'] is participant)

    def go(self, participant):
        participant.send('go')
        self.entry(participant)['started'] = True
        participant.wait('attempting', timeout=5)

    def settled(self, participant):
        result = participant.wait('settled', timeout=7)
        self.entry(participant)['settled'] = True
        self.record['observations'].append({'settled': result, 'python_pid': participant.process.pid})
        self.assertEqual(result['transaction_status'], 0)
        return result

    def release(self, holder, outcome='commit'):
        holder.send(outcome)
        settled = self.settled(holder)
        self.assertEqual(settled['outcome'], outcome)
        self.assertEqual(settled['queries'], [])
        self.assertIsNone(holder.process.poll(), 'Holder must remain alive after transaction exit')
        backend = observe_backend(self.observer, holder)
        self.assertEqual(backend['state'], 'idle')
        self.assertEqual(advisory_locks(self.observer, holder), [])
        self.record['observations'].append({'released_holder_still_connected': backend, 'outcome': outcome})

    def waiting(self, participant, holder):
        observation = observe_wait(self.observer, participant, timeout=3)
        blockers = self.observer.db.fetch_one(Query('SELECT pg_blocking_pids($1)', (participant.identity['pid'],)))[0]
        self.assertIn(holder.identity['pid'], blockers)
        self.assertIn((False, 'ExclusiveLock'), observation['locks'])
        self.record['observations'].append({'waiting': observation, 'blocking_pids': blockers})
        self.assertEqual(read_state(self.observer), self.initial)
        return observation

    def state(self):
        state = read_state(self.observer)
        self.record['observations'].append({'state': state})
        return state

    def cleanup_children(self):
        # Release holders before waiting on any queued writer. Never start an
        # unstarted writer during cleanup, never kill a process, never cancel an
        # arbitrary backend. All unresolved outcomes remain explicit evidence.
        failures = []
        for entry in self.children:
            participant = entry['participant']
            if entry['operation'] == 'hold' and not entry['settled'] and participant.process.poll() is None:
                try:
                    participant.send('rollback')
                    participant.wait('settled', timeout=7)
                    entry['settled'] = True
                except BaseException as error:
                    failures.append({'phase': 'release_holder', 'python_pid': participant.process.pid, 'error_type': type(error).__name__})
        for entry in self.children:
            participant = entry['participant']
            try:
                if entry['started'] and not entry['settled'] and participant.process.poll() is None:
                    participant.wait('settled', timeout=7)
                    entry['settled'] = True
                participant.close(timeout=5)
            except BaseException as error:
                failures.append({'phase': 'close', 'python_pid': participant.process.pid, 'error_type': type(error).__name__})
            finally:
                self.record['participants'].append({'operation': entry['operation'], 'identity': participant.identity,
                    'events': participant.events, 'returncode': participant.process.poll(), 'settled': entry['settled']})
        self.record['cleanup'] = failures
        if failures:
            self.fail('Bounded participant cleanup unresolved/unsuccessful: ' + json.dumps(failures, sort_keys=True))

    def test_actual_cross_process_contention_precedes_original_metadata_mutation(self):
        holder = self.child('hold')
        writer = self.child('metadata', expected_revision=0, name='Contended writer', description='')
        self.go(writer)
        self.waiting(writer, holder)
        self.release(holder)
        result = self.settled(writer)
        self.assertEqual(result['outcome'], 'commit')
        self.assertEqual(result['queries'], [{'sql': METADATA_SQL, 'params': ['analyst', 'Contended writer', ''], 'rowcount': 1}])
        state = self.state()
        self.assertEqual(state['agents'], [('analyst', 'Contended writer', None, False), ('builtin', 'Built in', None, True)])
        self.assertEqual(state['profiles'], self.initial['profiles'])
        self.assertEqual(state['revision'], (1, False))

    def test_two_queued_writers_refuse_second_stale_snapshot_before_original_sql(self):
        holder = self.child('hold')
        writers = [self.child('metadata', expected_revision=0, name=name, description='winner')
                   for name in ('First contender', 'Second contender')]
        for writer in writers:
            self.go(writer)
            self.waiting(writer, holder)
        self.release(holder)
        results = [self.settled(writer) for writer in writers]
        winners = [index for index, result in enumerate(results) if result['outcome'] == 'commit']
        self.assertEqual(len(winners), 1, 'No FIFO order is assumed')
        winner = winners[0]
        loser = results[1 - winner]
        self.assertEqual(loser['outcome'], 'error')
        self.assertEqual(loser['phase'], 'locked')
        self.assertEqual(loser['error']['type'], 'FixtureStale')
        self.assertEqual(loser['queries'], [])
        self.assertFalse(loser['reconciliation_required'])
        name = ('First contender', 'Second contender')[winner]
        self.assertEqual(results[winner]['queries'], [{'sql': METADATA_SQL, 'params': ['analyst', name, 'winner'], 'rowcount': 1}])
        state = self.state()
        self.assertEqual(state['agents'][0], ('analyst', name, 'winner', False))
        self.assertEqual(state['revision'], (1, False))

    def lock_release_case(self, outcome):
        holder = self.child('hold')
        writer = self.child('profile', expected_revision=0, profile_id=2)
        self.go(writer)
        self.waiting(writer, holder)
        self.release(holder, outcome)
        result = self.settled(writer)
        self.assertEqual(result['outcome'], 'commit')
        self.assertEqual(result['queries'], [
            {'sql': PROFILE_SQL[0], 'params': [], 'rowcount': 1},
            {'sql': PROFILE_SQL[1], 'params': [2], 'rowcount': 1},
        ])
        state = self.state()
        self.assertEqual(state['profiles'], [(1, False), (2, True)])
        self.assertEqual(state['revision'], (1, False))
        self.assertIsNone(holder.process.poll())
        self.assertEqual(advisory_locks(self.observer, holder), [])

    def test_commit_releases_transaction_lock_before_holder_connection_closes(self):
        self.lock_release_case('commit')

    def test_rollback_releases_transaction_lock_before_holder_connection_closes(self):
        self.lock_release_case('rollback')

    def test_cancel_registered_blocked_child_leaves_original_holder_and_rows_intact(self):
        holder = self.child('hold')
        writer = self.child('profile', expected_revision=0, profile_id=2)
        self.go(writer)
        self.waiting(writer, holder)
        signalled = cancel_waiter(self.observer, writer)
        self.assertIs(signalled, True)
        result = self.settled(writer)
        self.assertEqual(result['outcome'], 'error')
        self.assertEqual(result['phase'], 'before_lock')
        self.assertEqual(result['error'], {'type': 'QueryCanceled', 'sqlstate': '57014', 'cause': 'user_cancel'})
        self.assertEqual(result['queries'], [])
        self.assertFalse(result['reconciliation_required'])
        self.assertEqual(self.state(), self.initial)
        self.assertEqual(advisory_locks(self.observer, holder), [(True, 'ExclusiveLock')])
        self.assertEqual(advisory_locks(self.observer, writer), [])
        self.assertIsNone(holder.process.poll())
        self.record['observations'].append({'signal_sent': signalled, 'holder_after_cancel': observe_backend(self.observer, holder)})
        self.release(holder, 'rollback')

    def test_direct_original_profile_store_bypasses_cooperative_lock_and_revision(self):
        holder = self.child('hold')
        bypass = self.child('bypass_profile', profile_id=2)
        self.go(bypass)
        result = self.settled(bypass)
        self.assertEqual(result['outcome'], 'commit')
        self.assertEqual(result['queries'], [
            {'sql': PROFILE_SQL[0], 'params': [], 'rowcount': 1},
            {'sql': PROFILE_SQL[1], 'params': [2], 'rowcount': 1},
        ])
        state = self.state()
        self.assertEqual(state['profiles'], [(1, False), (2, True)])
        self.assertEqual(state['revision'], (0, False))
        self.assertEqual(advisory_locks(self.observer, holder), [(True, 'ExclusiveLock')])
        self.assertIsNone(holder.process.poll())
        self.record['observations'].append({'counterexample': 'Original store mutation bypasses proposed participating route',
            'holder_still_connected': observe_backend(self.observer, holder)})
        self.release(holder, 'rollback')
        self.assertEqual(self.state(), state, 'Holder rollback does not undo independent committed bypass')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--evidence', required=True)
    args = parser.parse_args()
    evidence_path = Path(args.evidence)
    if not evidence_path.is_absolute() or evidence_path.exists():
        parser.error('New absolute external evidence path required')
    config = load_fixture(args.config, args.sha256)
    OwnerWriterPostgresAdversarialTests.fixture = config
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OwnerWriterPostgresAdversarialTests)
    identifiers = [test.id() for test in suite]
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    evidence = {'config_path': config.path, 'config_sha256': config.sha256, 'tests': identifiers,
        'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'successful': result.wasSuccessful(), 'cases': OwnerWriterPostgresAdversarialTests.evidence,
        'boundary': 'Isolated PostgreSQL/original-store/cooperative-route evidence; genuine ART owner absent.'}
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
