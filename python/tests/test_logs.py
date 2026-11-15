from datetime import datetime, timezone
import unittest
from artex.db.logs import LogStore
from artex.server.logs import LogRing, LogService, parse_log
from artex.server.core import Router, Request


class DB:
    def __init__(self, rows=()):
        self.rows, self.calls = rows, []
    def fetch_all(self, q):
        self.calls.append(q)
        return self.rows
    def fetch_one(self, q):
        self.calls.append(q)
        return (7,)


class LogTests(unittest.TestCase):
    now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)

    def test_store_parameters_order_and_boundaries(self):
        db = DB([(2, self.now, 'warn', 'worker', 'second'), (1, self.now, 'info', '', 'first')])
        store = LogStore(db)
        self.assertEqual([x.id for x in store.recent(2)], [1, 2])
        self.assertEqual(db.calls[-1].params, (2,))
        store.before(3, 2)
        self.assertEqual(db.calls[-1].params, (3, 2))
        self.assertIn('id < $1', db.calls[-1].sql)
        self.assertEqual(store.insert('info', 'tag', "text'; DROP TABLE server_logs;--"), 7)
        self.assertNotIn('DROP TABLE', db.calls[-1].sql)
        self.assertIn('DROP TABLE', db.calls[-1].params[2])
        for limit in (0, -1, 501, True):
            with self.assertRaises(ValueError):
                store.recent(limit)
        self.assertEqual(store.recent(2)[0].to_dict()['ts'], '2026-11-15T12:00:00Z')

    def test_ring_parsing_cursors_and_capacity(self):
        ring = LogRing(2)
        for text in ('start', '2026/11/15 12:00:00 [worker] warn retry', 'error failure'):
            ring.append(parse_log(text, now=self.now))
        lines, cursor = ring.recent(0, 100)
        self.assertEqual(cursor, 3)
        self.assertEqual([line.seq for line in lines], [2, 3])
        self.assertEqual(lines[0].tag, 'worker')
        self.assertEqual(lines[0].level, 'warn')
        self.assertEqual(lines[1].level, 'error')
        self.assertEqual([line.seq for line in ring.recent(2, 1)[0]], [3])
        self.assertNotIn('db_id', lines[0].to_dict())
        self.assertEqual(parse_log('Operation denied', now=self.now).level, 'error')
        self.assertEqual(parse_log('Retry pending', now=self.now).level, 'warn')

    def test_authenticated_contracts_and_history(self):
        db = DB([(1, self.now, 'info', '', 'row')])
        ring = LogRing()
        ring.append(parse_log('hello', now=self.now))
        router = Router(lambda r: r.header('Authorization') == 'test')
        LogService(LogStore(db), ring).register(router)
        self.assertEqual(router.dispatch(Request('GET', '/api/logs')).status, 401)
        def request(path):
            return router.dispatch(Request('GET', path, {'Authorization': 'test'}))
        self.assertEqual(request('/api/logs').json()['cursor'], 1)
        history = request('/api/logs/history?before=2&limit=1').json()
        self.assertTrue(history['has_more'])
        self.assertEqual(history['items'][0]['seq'], 0)
        self.assertEqual(db.calls[-1].params, (2, 1))
        request('/api/logs/history?limit=1_0')
        self.assertEqual(db.calls[-1].params, (200,))
        request('/api/logs/history?limit=-5')
        self.assertEqual(db.calls[-1].params, (200,))
        request('/api/logs/history?limit=999')
        self.assertEqual(db.calls[-1].params, (500,))
        self.assertEqual(request('/api/logs/stream').status, 404)

    def test_missing_adapters_and_failures(self):
        svc = LogService()
        self.assertEqual(svc.recent(Request('GET','/api/logs')).status, 503)
        self.assertEqual(svc.history(Request('GET','/api/logs/history')).json(), {'items': [], 'has_more': False})
        svc.store = LogStore(DB([(1, 'not a timestamp', '', '', '')]))
        response = svc.history(Request('GET','/api/logs/history'))
        self.assertEqual(response.status, 500)
        self.assertNotIn('timestamp', response.body.decode())


if __name__ == '__main__':
    unittest.main()
