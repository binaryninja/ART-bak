"""Explicit local launcher for the single-instance read-only recon profile."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading
from wsgiref.simple_server import make_server

from .__main__ import _configuration
from .db.pool import ExecutorPool
from .db.profiles import ProfileStore
from .db.recording_schema import INSPECT_SQL, TABLES, _validate as validate_recording_table
from .db.core import Query
from .db.tasks import TaskStore
from .recon.servicenow import ServiceNowInventory
from .server.passwords import BcryptCodec
from .server.runtime_listener import DevListenerConfig, QuietRuntimeHandler, _BoundedServer
from .server.servicenow_recon import ReconRuntime, DESCRIPTION, GOAL

LOCK_KEY = 705481691502


def reconcile_preamble(pool, task_id):
    """After acquiring the exclusive DB owner, close a proved no-tool attempt.

    This does not adopt/replay a generation or clear an in-process quarantine.
    Its former process must have released the database ownership lock first.
    """
    with pool.transaction() as tx:
        row = tx.fetch_one(Query('SELECT exploration_id,description,goal,status FROM tasks WHERE id=$1 FOR UPDATE', (task_id,)))
        if row is None or row[1:] != (DESCRIPTION, GOAL, 'running'):
            raise RuntimeError('Preamble reconciliation target differs')
        evidence = tx.fetch_all(Query("SELECT kind,detail FROM activity WHERE exploration_id=$1 AND worker='servicenow-recon' "
            "AND kind IN ('recon_manifest','recon_observation','recon_report') ORDER BY id", (row[0],)))
        if [kind for kind, _ in evidence] != ['recon_manifest', 'recon_report']:
            raise RuntimeError('Preamble reconciliation requires no tool observations')
        manifest, report = (json.loads(item[1]) for item in evidence)
        if report.get('verified') is not False or report.get('observations') != [] or report.get('generation_id') != manifest.get('generation_id'):
            raise RuntimeError('Preamble report is not conclusive')
        records = tx.fetch_all(Query("SELECT status,session_id,response_body FROM llm_records WHERE task_id=$1 AND worker='servicenow-recon' ORDER BY id", (str(task_id),)))
        if len(records) != 1 or records[0][:2] != ('ok', manifest['session_id']):
            raise RuntimeError('Preamble provider evidence differs')
        response = json.loads(records[0][2])
        if response.get('stop_reason') != 'end_turn' or any(block.get('type') != 'text' for block in response['message']['content']):
            raise RuntimeError('Preamble contains tool or unknown provider effects')
        if TaskStore(tx).set_terminal_status_guarded(task_id, 'failed') is not True:
            raise RuntimeError('Preamble terminal write was not acknowledged')
        tx.execute(Query("INSERT INTO activity(exploration_id,worker,kind,detail) VALUES($1,'servicenow-recon','recon_reconciliation',$2)",
            (row[0], json.dumps({'generation_id': manifest['generation_id'], 'reason': 'verified preamble-only attempt; no replay'}))))
    result = TaskStore(pool).completion_lifecycle(task_id)
    if result['status'] != 'failed' or result['completed_at'] is None:
        raise RuntimeError('Preamble completion read-back failed')


def private_file(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('Canonical private credential file required')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('Credential file must be owned, regular, single-link, and mode 0600')
    return path.read_text().rstrip('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-id', required=True, type=int)
    parser.add_argument('--api-key-env', required=True)
    parser.add_argument('--username', default='admin')
    parser.add_argument('--password-file', required=True, type=Path)
    parser.add_argument('--transcript-root', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--frontend-origin', default='http://127.0.0.1:3000')
    parser.add_argument('--check-config', action='store_true')
    parser.add_argument('--reconcile-preamble-task', type=int)
    args = parser.parse_args(argv)
    dsn, signing_key = _configuration(os.environ)
    config = DevListenerConfig(args.port, 8, 30, args.frontend_origin)
    config.validate()
    root = args.transcript_root
    if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError('Existing canonical transcript directory required')
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Transcript directory must be owned and mode 0700')
    inventory = ServiceNowInventory(args.username, private_file(args.password_file))
    provider_key = os.environ[args.api_key_env]
    if not provider_key.strip() or args.profile_id <= 0:
        raise ValueError('Explicit provider credential and profile required')
    if args.check_config:
        print('Configuration syntax valid; database, provider and target were not contacted.')
        return 0
    import psycopg
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    lock = os.open(root / '.recon-owner.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with psycopg.connect(dsn, autocommit=True) as owner:
        if owner.execute('SELECT pg_try_advisory_lock(%s)', (LOCK_KEY,)).fetchone()[0] is not True:
            raise RuntimeError('Another recon runtime owns this database')
        pool = ExecutorPool.postgres(dsn, max_size=6)
        for table in TABLES:
            validate_recording_table(table, pool.fetch_one(Query(INSPECT_SQL, (table,))), final=True)
        if args.reconcile_preamble_task is not None:
            reconcile_preamble(pool, args.reconcile_preamble_task)
            print(json.dumps({'event': 'reconciled', 'task_id': args.reconcile_preamble_task, 'status': 'failed'}), flush=True)
        profile = ProfileStore(pool).get(args.profile_id)
        if profile is None:
            raise ValueError('Selected provider profile does not exist')
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        sources = hashlib.sha256()
        package_root = Path(__file__).resolve().parent
        for path in sorted(package_root.rglob('*.py')):
            sources.update(str(path.relative_to(package_root)).encode() + b'\0' + path.read_bytes())
        runtime = ReconRuntime(pool, profile, provider_key, inventory, root,
                               revision=revision, source_sha256=sources.hexdigest())
        app = runtime.application(signing_key, BcryptCodec())
        server = make_server('127.0.0.1', args.port, app, server_class=_BoundedServer, handler_class=QuietRuntimeHandler)
        server.configure(config, app.max_body_bytes)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=False)
        runtime.start_polling()
        thread.start()
        print(json.dumps({'event': 'ready', 'url': f'http://127.0.0.1:{args.port}', 'profile_id': profile.id,
                          'model': profile.model}), flush=True)
        stop.wait()
        runtime.host.stop_admissions()
        server.shutdown()
        thread.join(5)
        if not server.quiesce(30):
            raise RuntimeError('HTTP callbacks did not drain')
        server.server_close()
        result = runtime.close()
        if not pool.close(timeout=5).drained:
            raise RuntimeError('Database pool did not drain')
        print(json.dumps({'event': 'closed', 'drain': result}), flush=True)
    os.close(lock)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('Recon launcher failed: ' + type(exc).__name__, flush=True)
        raise SystemExit(1) from None
