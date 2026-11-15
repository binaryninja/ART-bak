import unittest,math
from artex.agent.taskclock import *
class TaskClockTests(unittest.TestCase):
    def test_no_deadline_preserves_budget_without_clock(self):
        for deadline in (0,-1):
            for budget in (0,-5,2.5):
                self.assertEqual(clamp_max_duration(deadline,budget,wall_clock=lambda:self.fail('clock called')),(budget,False))
    def test_source_clamp_floor_equality_and_subsecond_own_budget(self):
        cases=[(110,20,10,True),(110,5,5,False),(110,10,10,True),(110,0,10,True),(110,-3,10,True),(99,20,1,True),(99,.5,.5,False),(100,0,1,True)]
        for deadline,budget,eff,clamped in cases:
            with self.subTest(deadline=deadline,budget=budget):self.assertEqual(clamp_max_duration(deadline,budget,wall_clock=lambda:100),(eff,clamped))
    def test_fractional_wallclock_one_read(self):
        calls=[]
        self.assertEqual(clamp_max_duration(110,0,wall_clock=lambda:calls.append(1) or 100.25),(9.75,True));self.assertEqual(calls,[1])
    def test_finite_validation_and_explicit_clock(self):
        for value in (True,float('inf'),float('nan'),'4'):
            with self.assertRaises(ValueError):clamp_max_duration(110,value,wall_clock=lambda:100)
            with self.assertRaises(ValueError):clamp_max_duration(110,1,wall_clock=lambda:value)
        with self.assertRaises(ValueError):clamp_max_duration(110,0)
        with self.assertRaises(ValueError):clamp_max_duration(True,1)
    def test_metadata_immutable_and_default(self):
        original={'x':1};tc=TaskClock(123,True);new=with_task_clock(original,tc)
        self.assertEqual(original,{'x':1});self.assertEqual(task_clock_from(new),tc);self.assertEqual(task_clock_from(original),TaskClock())
        with self.assertRaises(ValueError):TaskClock(1,1)
