#!/usr/bin/env python3
"""Parse SQL constructed by authored Python tests, without executing a database.

Requires optional pglast (tested 8.8). This is syntax-only coverage of statements
constructed by tests, not schema/type/permission/transaction or live-driver proof.
It never imports or runs the upstream Go application or its scripts.
"""
from pathlib import Path
import re
import sys
from threading import Lock
import unittest


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    try:
        from pglast import parse_sql
    except ImportError:
        print('Optional SQL syntax QA requires pglast (tested version 8.8).', file=sys.stderr)
        return 2
    from artex.db.core import Query
    original = Query.__init__
    statements, lock = set(), Lock()

    def record(self, sql, params=()):
        original(self, sql, params)
        if isinstance(sql, str) and re.match(r'\s*(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|ALTER)\b', sql, re.I):
            with lock:
                statements.add(sql)

    Query.__init__ = record
    try:
        result = unittest.TextTestRunner(verbosity=0).run(
            unittest.defaultTestLoader.discover(str(root / 'tests')))
    finally:
        Query.__init__ = original
    errors = []
    for sql in sorted(statements):
        try:
            parse_sql(sql)
        except Exception as error:
            errors.append((sql, str(error)))
    print(f'Parsed {len(statements)} unique SQL statements from authored tests; {len(errors)} syntax failures.')
    for sql, error in errors:
        print(f'{error}\n{sql}\n', file=sys.stderr)
    return int(bool(errors) or not result.wasSuccessful())


if __name__ == '__main__':
    raise SystemExit(main())
