"""Parameter-bound server_logs persistence, ported from db/logs.go.

No connection is opened. Queries return newest-first rows from PostgreSQL and
reverse them for oldest-first API display. SQL/driver errors propagate.
"""
from dataclasses import dataclass
from datetime import datetime
from .core import Query, int64


@dataclass(frozen=True)
class LogRecord:
    id: int
    created_at: datetime
    level: str
    tag: str
    text: str

    def to_dict(self):
        return {'seq': 0, 'db_id': self.id,
                'ts': self.created_at.isoformat(timespec='seconds').replace('+00:00', 'Z'),
                'level': self.level, 'tag': self.tag, 'text': self.text}


def _row(row):
    if len(row) != 5 or type(row[0]) is not int or not isinstance(row[1], datetime) or not all(isinstance(v, str) for v in row[2:]):
        raise ValueError('Malformed server log row')
    return LogRecord(int64(row[0], 'log ID'), *row[1:])


class LogStore:
    def __init__(self, executor):
        self.executor = executor

    def insert(self, level, tag, text):
        if not all(isinstance(value, str) for value in (level, tag, text)):
            raise TypeError('Log fields must be strings')
        row = self.executor.fetch_one(Query('INSERT INTO server_logs(level,tag,text) VALUES($1,$2,$3) RETURNING id', (level, tag, text)))
        if len(row) != 1:
            raise ValueError('Malformed inserted log ID')
        return int64(row[0], 'log ID')

    def recent(self, limit):
        return self._list(None, limit)

    def before(self, before_id, limit):
        return self._list(int64(before_id, 'before ID'), limit)

    def _list(self, before, limit):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('Log history limit must be between 1 and 500')
        if before is None:
            query = Query('SELECT id, created_at, level, tag, text FROM server_logs ORDER BY id DESC LIMIT $1', (limit,))
        else:
            query = Query('SELECT id, created_at, level, tag, text FROM server_logs WHERE id < $1 ORDER BY id DESC LIMIT $2', (before, limit))
        return list(reversed([_row(row) for row in self.executor.fetch_all(query)]))
