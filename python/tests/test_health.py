import unittest
from artex.health import Registry, State


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.registry = Registry(clock=lambda: self.now)

    def test_default_threshold_ladder_and_expiry(self):
        r = self.registry
        self.assertFalse(r.trip(1, "429"))
        self.assertFalse(r.trip(1, "429"))
        self.assertTrue(r.trip(1, "429"))
        self.assertEqual(r.get(1).open_until, 1060)
        self.assertTrue(r.is_open(1))
        self.now = 1060
        self.assertFalse(r.is_open(1))
        for duration in (300, 1800, 1800):
            self.assertTrue(r.trip(1, "invalid key", hard=True))
            self.assertEqual(r.get(1).open_until, self.now + duration)

    def test_policy_overrides(self):
        r = self.registry
        r.set_policy(2, 90)
        self.assertFalse(r.trip(1, "429"))
        self.assertTrue(r.trip(1, "429"))
        self.assertEqual(r.get(1).open_until, 1090)
        r.set_policy(-1, 0)
        for _ in range(10):
            self.assertFalse(r.trip(2, "429"))
        self.assertFalse(r.is_open(2))
        self.assertTrue(r.trip(2, "bad key", True))

    def test_copies_restore_reset_and_callbacks(self):
        saved, forgotten = [], []
        r = Registry(lambda i, st: saved.append((i, st)), forgotten.append, lambda: self.now)
        original = State(trips=1, open_until=1100)
        r.restore(1, original)
        original.trips = 99
        r.get(1).trips = 99
        r.snapshot()[1].trips = 99
        self.assertEqual(r.get(1).trips, 1)
        self.assertFalse(saved)
        r.trip(1, "bad key", True)
        saved[0][1].trips = 99
        self.assertEqual(r.get(1).trips, 2)
        r.pass_(1)
        r.pass_(1)
        self.assertEqual(forgotten, [1])
        self.assertEqual(r.get(1), State())
        r.reset(1)
        self.assertEqual(forgotten, [1, 1])

    def test_callbacks_run_outside_lock(self):
        r = Registry(lambda i, st: r.get(i))
        self.assertTrue(r.trip(1, "bad key", True))


if __name__ == "__main__":
    unittest.main()
