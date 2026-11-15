"""Independent registry/SQL lock ordering, synthetic repository only."""
import unittest
from concurrent.futures import ThreadPoolExecutor
import test_task_deadline as fixture

class Review(unittest.TestCase):
    def test_pause_persistence_does_not_hold_registry_lock(self):
        f=fixture.DeadlineTests();f.setUp();self.addCleanup(f.doCleanups)
        host=f.f.f.host;manager=host.manager
        original=manager.repository.apply_pause
        with ThreadPoolExecutor(max_workers=1) as pool:
            def pause(task_id):
                # Simulate another transaction's registry validation while pause
                # waits for persistence. No real SQL or indefinite deadlock.
                pool.submit(manager.deadline_owners).result(0.5)
                return original(task_id)
            manager.repository.apply_pause=pause
            host.pause(f.handle.task_id)
