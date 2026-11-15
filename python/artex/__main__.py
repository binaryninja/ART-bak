"""Explicit loopback-only development API launcher, not a production server.

Usage: PYTHONPATH=python python -m artex --check-config
       PYTHONPATH=python python -m artex --serve --port 8787
Supply ARTEX_PG_DSN and ARTEX_SIGNING_KEY_HEX externally through the environment.
The signing key must be hexadecimal encoding of at least 32 bytes; it is never generated,
saved or printed. --check-config does not import optional drivers or connect.
--serve needs official psycopg >=3.2 and PyCA bcrypt >=5.0,<6 already installed,
and an existing compatible PostgreSQL schema. No migrations, frontend hosting,
agent runner, providers, tools or automatic task resumption are enabled. The
stdlib development server is single-threaded, unencrypted, and only binds IPv4
127.0.0.1. Do not expose it through proxies or use it for production. HTTP request
logging is suppressed to avoid disclosing query parameters or credentials.
"""
from __future__ import annotations

import argparse
import os
import sys
from wsgiref.simple_server import make_server, WSGIRequestHandler
from .db.postgres import PostgresExecutor, _driver
from .server.application import build_persisted_application
from .server.passwords import BcryptCodec


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def _configuration(environ):
    dsn = environ.get('ARTEX_PG_DSN', '')
    key = environ.get('ARTEX_SIGNING_KEY_HEX', '')
    if not isinstance(dsn, str) or not dsn.strip() or '\x00' in dsn:
        raise ValueError('ARTEX_PG_DSN is required and must be nonempty')
    try:
        encoded = bytes.fromhex(key)
        if len(key) != len(encoded) * 2:
            raise ValueError('Noncanonical hex')
    except (TypeError, ValueError):
        raise ValueError('ARTEX_SIGNING_KEY_HEX must be hexadecimal without whitespace') from None
    if len(encoded) < 32:
        raise ValueError('ARTEX_SIGNING_KEY_HEX must encode at least 32 bytes')
    return dsn, encoded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--serve', action='store_true', help='Start the loopback-only development API')
    mode.add_argument('--check-config', action='store_true', help='Validate environment only; no connections')
    parser.add_argument('--port', type=int, default=8787, help='Loopback TCP port, 1..65535 (default 8787)')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    try:
        dsn, key = _configuration(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)  # fixed messages only, no values
        return 2
    if args.check_config:
        print('Configuration valid. Dependencies, database and schema were not checked.')
        return 0
    codec = BcryptCodec()
    try:
        _driver()
        codec._backend()  # Dependency availability only; no hashing or credentials.
    except Exception:
        print('Required dependencies unavailable: official psycopg >=3.2 and PyCA bcrypt >=5.0,<6.', file=sys.stderr)
        return 2
    try:
        with PostgresExecutor.connect(dsn) as executor:
            app = build_persisted_application(executor, key, codec)
            with make_server('127.0.0.1', args.port, app, handler_class=QuietHandler) as server:
                print(f'Development API at http://127.0.0.1:{args.port}; no task execution or frontend.')
                server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('Development API startup or serving failed. Check local database, schema and port configuration.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
