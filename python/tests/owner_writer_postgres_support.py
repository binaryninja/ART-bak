"""Opt-in, TEST-ONLY isolated PostgreSQL participating-writer experiment.

No database is discovered or opened on import. A root-attested configuration and
its separately supplied SHA-256 are mandatory. Advisory locks coordinate only
this fixture's participating clients, not ART authority or direct store callers.
The fixture revision is not a task/run/policy revision. A transaction rollback
also rolls back its revision update. Admission failures and uncertain outcomes
must therefore never be interpreted as durable revocation of ART authority.

Order: original executor transaction -> transaction advisory lock -> separate
fresh revision SELECT -> original store mutation -> fixture revision UPDATE ->
transaction exit. READ COMMITTED is explicit. Cancellation is a separately
observed signal; it does not prove rollback. Reconciliation is explicit and may
fail; it is not production recovery. No process is killed by this support.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time
import uuid

from artex.db.core import Query
from artex.db.postgres import PostgresExecutor
from artex.db.profiles import ProfileStore
from artex.db.agents import AgentStore


class FixtureRefused(ValueError): pass
class FixtureStale(RuntimeError): pass
class FixtureTimeout(TimeoutError): pass
class FixtureRollback(RuntimeError): pass


_FIELDS = frozenset(('schema_version', 'fixture_id', 'socket_directory', 'port',
    'database', 'user', 'password', 'schema', 'control_schema', 'marker',
    'advisory_key', 'expected_server_major', 'expected_server_version_num',
    'system_identifier'))
_IDENTIFIER = re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
_STORE_SQL = frozenset((
    'UPDATE llm_profiles SET is_default=false WHERE is_default',
    'UPDATE llm_profiles SET is_default=true WHERE id=$1',
    "UPDATE agents SET name=$2, description=NULLIF($3,'') WHERE key=$1 AND builtin=false"))


@dataclass(frozen=True, repr=False)
class FixtureConfig:
    path: str
    sha256: str
    values: dict


def load_fixture(path, expected_sha256):
    """Validate explicit bytes and exact shape before any driver import/connect."""
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise FixtureRefused('Explicit configuration SHA-256 required')
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise FixtureRefused('Explicit absolute configuration path required')
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise FixtureRefused('Configuration hash mismatch')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise FixtureRefused('Invalid configuration JSON') from None
    if type(value) is not dict or set(value) != _FIELDS:
        raise FixtureRefused('Invalid configuration fields')
    for name in ('database', 'user', 'schema'):
        if type(value[name]) is not str or not _IDENTIFIER.fullmatch(value[name]):
            raise FixtureRefused('Invalid fixture identifier')
    if value['schema'] != value['user'] or not value['schema'].startswith('fixture_'):
        raise FixtureRefused('Isolated role/schema required')
    for name in ('password', 'fixture_id', 'marker', 'system_identifier'):
        if type(value[name]) is not str or not value[name] or '\0' in value[name]:
            raise FixtureRefused('Invalid fixture identity')
    socket = value['socket_directory']
    if type(socket) is not str or not socket.startswith('/') or ',' in socket or '\0' in socket:
        raise FixtureRefused('Single explicit Unix socket directory required')
    if type(value['port']) is not int or not 1 <= value['port'] <= 65535:
        raise FixtureRefused('Invalid fixture port')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1 or
            value['control_schema'] != 'fixture_control' or
            type(value['expected_server_major']) is not int or value['expected_server_major'] != 17 or
            type(value['expected_server_version_num']) is not int or
            value['expected_server_version_num'] // 10000 != 17):
        raise FixtureRefused('Invalid fixture version')
    keys = value['advisory_key']
    if type(keys) is not list or len(keys) != 2 or any(type(k) is not int or not -(2**31) <= k < 2**31 for k in keys):
        raise FixtureRefused('Two int32 advisory keys required')
    return FixtureConfig(str(Path(path)), expected_sha256, value)


def _timeout(value, maximum=20):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
        raise FixtureRefused('Positive bounded timeout required')
    return float(value)


class FixtureSession:
    def __init__(self, config, connection, db, identity, queries):
        self.config, self.connection, self.db = config, connection, db
        self.identity, self.queries = identity, queries

    def close(self):
        # Only same-thread idle session cleanup; no cross-thread adapter close.
        if self.connection.info.transaction_status != 0:
            raise FixtureRefused('Refusing cleanup of unsettled transaction')
        self.db.close()
        self.connection.close()

    def __enter__(self): return self
    def __exit__(self, *error): self.close(); return False


def connect_fixture(config):
    """Connect only to revalidated attested Unix fixture, then verify identity."""
    if type(config) is not FixtureConfig:
        raise FixtureRefused('Loaded fixture configuration required')
    # Ignore mutable dataclass contents: reload original hash-bound bytes.
    config = load_fixture(config.path, config.sha256)
    if any(name.startswith('PG') for name in os.environ):
        raise FixtureRefused('Ambient PostgreSQL environment is forbidden')
    import psycopg
    from psycopg.rows import tuple_row
    v = config.values
    application = 'ow_fixture_' + uuid.uuid4().hex
    conn = psycopg.connect('', host=v['socket_directory'], port=v['port'],
        dbname=v['database'], user=v['user'], password=v['password'],
        passfile='/dev/null', sslmode='disable', connect_timeout=3,
        application_name=application, autocommit=True,
        options='-c search_path=' + v['schema'] + ',pg_catalog'
            ' -c default_transaction_isolation=read\\ committed'
            ' -c statement_timeout=6000 -c lock_timeout=4000'
            ' -c idle_in_transaction_session_timeout=15000')
    queries = []

    class ObservedCursor(psycopg.RawCursor):
        def execute(self, query, params=None, **kwargs):
            result = super().execute(query, params, **kwargs)
            if query in _STORE_SQL:
                queries.append({'sql': query, 'params': list(params or ()), 'rowcount': self.rowcount})
            return result

    try:
        db = PostgresExecutor(conn, cursor_factory=lambda c: ObservedCursor(c, row_factory=tuple_row))
        actual = db.fetch_one(Query("SELECT current_database(), current_user, current_setting('server_version_num')::int, r.rolsuper, n.nspowner=r.oid FROM pg_roles r JOIN pg_namespace n ON n.nspname=$1 WHERE r.rolname=current_user", (v['schema'],)))
        if actual != (v['database'], v['user'], v['expected_server_version_num'], False, True):
            raise FixtureRefused('Fixture server/role/schema identity mismatch')
        row = db.fetch_one(Query('SELECT fixture_id,participant,schema_name,marker,system_identifier FROM fixture_control.identity WHERE participant=$1', (v['user'],)))
        if row != tuple(v[k] for k in ('fixture_id', 'user', 'schema', 'marker', 'system_identifier')):
            raise FixtureRefused('Root fixture marker mismatch')
        backend = db.fetch_one(Query('SELECT pid,backend_start::text,application_name,datname,usename FROM pg_stat_activity WHERE pid=pg_backend_pid()'))
        identity = dict(zip(('pid', 'backend_start', 'application_name', 'database', 'user'), backend))
        identity.update(python_pid=os.getpid(), fixture_id=v['fixture_id'], schema=v['schema'])
        return FixtureSession(config, conn, db, identity, queries)
    except BaseException:
        conn.close()
        raise


def prepare_schema(session):
    """Create exactly three minimal tables in an empty, attested owned schema.

    DDL uses the owned driver cursor: original executor.execute correctly refuses
    DDL's absent affected-row count. Existing schemas are only accepted if their
    table names and private marker already match; unrelated objects are refused.
    """
    schema = session.config.values['schema']
    objects = session.db.fetch_all(Query('SELECT relname,relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=$1', (schema,)))
    if objects:
        tables = {name for name, kind in objects if kind == 'r'}
        if tables != {'llm_profiles', 'agents', 'fixture_state'} or any(kind not in ('r', 'i') for _, kind in objects):
            raise FixtureRefused('Fixture schema is not empty or expected')
        marker = session.db.fetch_one(Query('SELECT marker FROM fixture_state WHERE id=1'))[0]
        if marker != session.config.values['marker']:
            raise FixtureRefused('Fixture table marker mismatch')
        return
    with session.db.transaction():
        with session.connection.cursor() as cursor:
            cursor.execute('CREATE TABLE llm_profiles(id bigint PRIMARY KEY,is_default boolean NOT NULL)')
            cursor.execute('CREATE TABLE agents(key text PRIMARY KEY,name text NOT NULL,description text,builtin boolean NOT NULL)')
            cursor.execute('CREATE TABLE fixture_state(id integer PRIMARY KEY CHECK(id=1),marker text NOT NULL,revision bigint NOT NULL,uncertain boolean NOT NULL)')
            cursor.execute('INSERT INTO fixture_state VALUES (1,%s,0,false)', (session.config.values['marker'],))


def acquire_lock(session):
    if session.connection.info.transaction_status == 0:
        raise FixtureRefused('Explicit original executor transaction required')
    session.db.fetch_one(Query('SELECT pg_advisory_xact_lock($1,$2)', tuple(session.config.values['advisory_key'])))


def reset_rows(session):
    with session.db.transaction():
        acquire_lock(session)
        session.db.execute(Query('DELETE FROM llm_profiles'))
        session.db.execute(Query('INSERT INTO llm_profiles VALUES (1,true),(2,false)'))
        session.db.execute(Query('DELETE FROM agents'))
        session.db.execute(Query("INSERT INTO agents VALUES ('analyst','Initial','initial',false),('builtin','Built in',NULL,true)"))
        session.db.execute(Query('UPDATE fixture_state SET revision=0,uncertain=false WHERE id=1 AND marker=$1', (session.config.values['marker'],)))
    session.queries.clear()


def read_state(session):
    return {'profiles': session.db.fetch_all(Query('SELECT id,is_default FROM llm_profiles ORDER BY id')),
        'agents': session.db.fetch_all(Query('SELECT key,name,description,builtin FROM agents ORDER BY key')),
        'revision': session.db.fetch_one(Query('SELECT revision,uncertain FROM fixture_state WHERE id=1'))}


def reconcile_uncertain(session, expected_revision):
    """Explicit conservative fixture poison after settlement, never automatic retry."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise FixtureRefused('Invalid expected revision')
    with session.db.transaction():
        acquire_lock(session)
        revision, _ = session.db.fetch_one(Query('SELECT revision,uncertain FROM fixture_state WHERE id=1'))
        if revision < expected_revision:
            raise FixtureRefused('Fixture revision regressed')
        session.db.execute(Query('UPDATE fixture_state SET revision=revision+1,uncertain=true WHERE id=1'))


def safe_error(error):
    code = getattr(error, 'sqlstate', None)
    primary = getattr(getattr(error, 'diag', None), 'message_primary', None)
    cause = { 'canceling statement due to user request': 'user_cancel',
        'canceling statement due to statement timeout': 'statement_timeout',
        'canceling statement due to lock timeout': 'lock_timeout' }.get(primary, 'other')
    return {'type': type(error).__name__, 'sqlstate': code, 'cause': cause}


def _emit(event, **values):
    payload = (json.dumps(dict(event=event, **values)) + '\n').encode()
    if len(payload) > 262144:
        raise FixtureRefused('Participant event size exceeds bound')
    descriptor = sys.stdout.fileno()
    os.set_blocking(descriptor, False)
    deadline = time.monotonic() + 3
    while payload:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([], [descriptor], [], max(0, remaining))[1]:
            raise FixtureTimeout('Participant event send deadline expired')
        try:
            count = os.write(descriptor, payload)
        except BlockingIOError:
            continue
        payload = payload[count:]


_CONTROL_BUFFER = b''


def _command(timeout=12):
    global _CONTROL_BUFFER
    deadline = time.monotonic() + _timeout(timeout)
    while b'\n' not in _CONTROL_BUFFER:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin], [], [], max(0, remaining))[0]:
            raise FixtureTimeout('Child control timeout')
        chunk = os.read(sys.stdin.fileno(), 4096)
        if not chunk:
            raise FixtureRefused('Child control closed')
        _CONTROL_BUFFER += chunk
        if len(_CONTROL_BUFFER) > 65536:
            raise FixtureRefused('Child command exceeds bound')
    line, _CONTROL_BUFFER = _CONTROL_BUFFER.split(b'\n', 1)
    return json.loads(line)


def _child(path, sha, operation, arguments):
    with connect_fixture(load_fixture(path, sha)) as session:
        _emit('connected', identity=session.identity)
        closing = False
        try:
            if operation == 'hold':
                outcome = 'commit'
                try:
                    with session.db.transaction():
                        acquire_lock(session)
                        _emit('held')
                        command = _command()
                        if command in ('rollback', 'close'):
                            closing = command == 'close'
                            raise FixtureRollback()
                        if command != 'commit':
                            raise FixtureRefused('Expected holder commit or rollback')
                except FixtureRollback:
                    outcome = 'rollback'
                _emit('settled', outcome=outcome, transaction_status=int(session.connection.info.transaction_status), queries=session.queries)
            else:
                command = _command()
                if command == 'close':
                    return
                if command != 'go':
                    raise FixtureRefused('Explicit go required')
                _emit('attempting')
                phase = 'before_lock'
                try:
                    if operation.startswith('bypass_'):
                        _store_write(session, operation[7:], arguments)
                    else:
                        with session.db.transaction():
                            acquire_lock(session)
                            phase = 'locked'
                            _emit('acquired')
                            revision, uncertain = session.db.fetch_one(Query('SELECT revision,uncertain FROM fixture_state WHERE id=1'))
                            expected = arguments.get('expected_revision')
                            if type(expected) is not int or expected < 0:
                                raise FixtureRefused('Explicit nonnegative revision required')
                            if uncertain or revision != expected:
                                raise FixtureStale('Fixture revision refused')
                            phase = 'store'
                            _store_write(session, operation, arguments)
                            _emit('mutated', queries=session.queries)
                            session.db.execute(Query('UPDATE fixture_state SET revision=revision+1 WHERE id=1'))
                            phase = 'transaction_exit'
                    _emit('settled', outcome='commit', transaction_status=int(session.connection.info.transaction_status), queries=session.queries)
                except BaseException as error:
                    # IDLE plus error is not proof of rollback after uncertain commit.
                    _emit('settled', outcome='error', phase=phase, error=safe_error(error),
                        transaction_status=int(session.connection.info.transaction_status), queries=session.queries,
                        reconciliation_required=phase in ('store', 'transaction_exit'))
            if not closing and _command() != 'close':
                raise FixtureRefused('Expected explicit close')
        except BaseException as error:
            _emit('fatal', error=safe_error(error))
            raise
    _emit('closed')


def _store_write(session, operation, arguments):
    if operation == 'profile':
        ProfileStore(session.db).set_active(arguments.get('profile_id', 2))
    elif operation == 'metadata':
        AgentStore(session.db).update_metadata(arguments.get('key', 'analyst'), arguments.get('name', 'Updated'), arguments.get('description', ''))
    else:
        raise FixtureRefused('Unknown fixture operation')


class Participant:
    """One fresh subprocess/connection; bounded waits, no terminate/kill fallback."""
    def __init__(self, path, sha256, operation, **arguments):
        config = load_fixture(path, sha256)
        if operation not in ('hold', 'profile', 'metadata', 'bypass_profile', 'bypass_metadata'):
            raise FixtureRefused('Unknown fixture operation')
        if len(json.dumps(arguments).encode()) > 65536:
            raise FixtureRefused('Participant arguments exceed bound')
        self.config, self.identity, self.events = config, None, []
        self._buffer = b''
        self._pending = []
        # Do not copy the parent environment, credentials or PG routing defaults.
        python_root = str(Path(__file__).resolve().parents[1])
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': python_root}
        self.process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()),
            '--child', config.path, sha256, operation, json.dumps(arguments)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, close_fds=True, bufsize=0)
        os.set_blocking(self.process.stdin.fileno(), False)

    def send(self, command, timeout=2):
        if command not in ('go', 'commit', 'rollback', 'close'):
            raise FixtureRefused('Invalid participant control')
        payload = (json.dumps(command) + '\n').encode()
        deadline = time.monotonic() + _timeout(timeout)
        while payload:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [self.process.stdin], [], max(0, remaining))[1]:
                raise FixtureTimeout('Participant send deadline expired')
            try:
                count = os.write(self.process.stdin.fileno(), payload)
            except BlockingIOError:
                continue
            payload = payload[count:]

    def wait(self, event, timeout=8):
        deadline = time.monotonic() + _timeout(timeout)
        while True:
            for index, item in enumerate(self._pending):
                if item['event'] == event:
                    return self._pending.pop(index)
                if item['event'] == 'fatal':
                    raise FixtureRefused('Child reported classified fatal error')
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], max(0, remaining))[0]:
                raise FixtureTimeout('Participant event deadline expired')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise FixtureRefused('Participant exited before required event')
            self._buffer += chunk
            if len(self._buffer) > 262144:
                raise FixtureRefused('Participant output limit exceeded')
            while b'\n' in self._buffer:
                line, self._buffer = self._buffer.split(b'\n', 1)
                item = json.loads(line)
                self.events.append(item)
                self._pending.append(item)
                if item['event'] == 'connected':
                    if self.identity is not None:
                        raise FixtureRefused('Repeated participant identity')
                    self.identity = item['identity']

    def close(self, timeout=5):
        """Call after settled (or explicitly release holder); report unresolved PID."""
        timeout = _timeout(timeout)
        if self.process.poll() is None:
            self.send('close')
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise FixtureTimeout('Participant unresolved; root cleanup required') from None
        for pipe in (self.process.stdin, self.process.stdout):
            pipe.close()
        if self.process.returncode != 0:
            raise FixtureRefused('Participant exited unsuccessfully')


def observe_backend(observer, participant):
    if type(participant) is not Participant or participant.identity is None:
        raise FixtureRefused('Registered connected participant required')
    if participant.process.poll() is not None:
        raise FixtureRefused('Participant process has exited')
    identity = participant.identity
    if (participant.config.sha256 != observer.config.sha256 or
            identity['python_pid'] != participant.process.pid or
            identity['fixture_id'] != observer.config.values['fixture_id'] or
            identity['schema'] != observer.config.values['schema']):
        raise FixtureRefused('Foreign fixture participant')
    rows = observer.db.fetch_all(Query('SELECT pid,backend_start::text,application_name,datname,usename,state,wait_event_type,wait_event FROM pg_stat_activity WHERE pid=$1 AND backend_start::text=$2 AND application_name=$3 AND datname=$4 AND usename=$5', tuple(identity[k] for k in ('pid', 'backend_start', 'application_name', 'database', 'user'))))
    if len(rows) != 1:
        raise FixtureRefused('Participant backend identity no longer matches')
    return dict(zip(('pid','backend_start','application_name','database','user','state','wait_event_type','wait_event'), rows[0]))


def advisory_locks(observer, participant):
    observe_backend(observer, participant)
    keys = observer.config.values['advisory_key']
    return observer.db.fetch_all(Query("SELECT granted,mode FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND classid::bigint=$2 AND objid::bigint=$3 AND objsubid=2 AND database=(SELECT oid FROM pg_database WHERE datname=current_database())", (participant.identity['pid'], keys[0] % 2**32, keys[1] % 2**32)))


def observe_wait(observer, participant, timeout=3):
    deadline = time.monotonic() + _timeout(timeout)
    while time.monotonic() < deadline:
        backend = observe_backend(observer, participant)
        locks = advisory_locks(observer, participant)
        if backend['wait_event_type'] == 'Lock' and backend['wait_event'] == 'advisory' and any(not granted for granted, _ in locks):
            return dict(backend=backend, locks=locks)
        time.sleep(.01)
    raise FixtureTimeout('Positive advisory wait not observed')


def cancel_waiter(observer, participant):
    """Identity-check then PID signal; not atomic compare-and-cancel or rollback."""
    observe_wait(observer, participant, timeout=1)
    observe_backend(observer, participant)
    return observer.db.fetch_one(Query('SELECT pg_cancel_backend($1)', (participant.identity['pid'],)))[0]


if __name__ == '__main__':
    if len(sys.argv) != 6 or sys.argv[1] != '--child':
        raise SystemExit('Explicit fixture child protocol only')
    try:
        _child(sys.argv[2], sys.argv[3], sys.argv[4], json.loads(sys.argv[5]))
    except BaseException:
        raise SystemExit(1)
