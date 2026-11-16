"""Cleanup projection is optional, never a caller-supplied current override."""
import unittest
import test_activity_projection as fixture
from artex.agent.activity_projection import ProjectionUnavailable
from artex.agent.inflight_cleanup import CleanupTurn
class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def call(self,cap):
        f=self.f;return f.p.project('planner',f.s,f.outcome,f.identity,cleanup=cap)
    def test_foreign_capability_cannot_replace_lifetime(self):
        self.f.live=False
        for cap in (True,object(),CleanupTurn(object(),'5',self.f.turn.id)):
            with self.assertRaises(ProjectionUnavailable):self.call(cap)
        self.assertEqual(self.f.db.rows,[])
    def test_no_capability_preserves_original_lifetime_and_grants(self):
        self.f.live=False
        with self.assertRaises(ProjectionUnavailable):self.call(None)
        self.f.live=True;self.f.allowed=False
        with self.assertRaises(ProjectionUnavailable):self.call(None)
        self.assertEqual(self.f.db.rows,[])
