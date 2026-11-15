"""Independent coordinator completion tests, synthetic registry only."""
import unittest
import test_pool_health as fixtures
from artex.health import Registry,State

class PoolHealthReview(unittest.TestCase):
    def test_swallowed_apply_failure_cannot_report_success(self):
        for phase in ('idle','forget'):
            with self.subTest(phase=phase):
                fixture=fixtures.PoolTests()
                def fail_forget(id):raise RuntimeError('synthetic private failure')
                registry=Registry(forget=fail_forget if phase=='forget' else None)
                service=fixture.make([[],[],[]],registry)
                registry.restore(7,State(1))
                if phase=='idle':
                    checks=iter((True,False))
                    service.idle=lambda *a:next(checks)
                def coordinate(change,apply):
                    try:apply()
                    except Exception:pass
                    return True
                service.coordinate=coordinate
                response=fixture.call('reset',{'id':7})
                self.assertEqual(response.status,503)
                self.assertTrue(response.json()['needs_reconciliation'])
    def test_nonfinite_timestamp_is_not_serialized(self):
        for value in (float('nan'),float('inf'),float('-inf')):
            fixture=fixtures.PoolTests();fixture.make([[],[],[fixtures.PROFILE]])
            fixture.registry.restore(7,State(last_at=value))
            response=fixture.call('status')
            self.assertEqual(response.status,503)
            self.assertNotIn(b'NaN',response.body)

    def test_cross_thread_apply_cannot_reset_even_during_coordinator(self):
        from threading import Thread
        fixture=fixtures.PoolTests();service=fixture.make([])
        fixture.registry.restore(7,State(1));failures=[]
        def coordinate(change,apply):
            def worker():
                try:apply()
                except Exception as exc:failures.append(type(exc))
            thread=Thread(target=worker);thread.start();thread.join()
            return True
        service.coordinate=coordinate
        self.assertEqual(fixture.call('reset',{'id':7}).status,503)
        self.assertEqual(len(failures),1)
        self.assertEqual(fixture.registry.get(7).fails,1)
        self.assertEqual(fixture.db.calls,[])
