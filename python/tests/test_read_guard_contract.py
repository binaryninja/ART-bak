import unittest
from contextlib import contextmanager
import test_command_history as commands
import test_token_usage as tokens
class GuardContractTests(unittest.TestCase):
    def test_direct_reads_do_not_reenter_guard(self):
        for command in (True,False):
            active=[False]
            @contextmanager
            def guard(scope):
                if active[0]:raise AssertionError('Guard reentered')
                active[0]=True
                try:yield
                finally:active[0]=False
            if command:
                f=commands.CommandTests();s=f.make([[commands.ROW]],False);operation=lambda:s.list()
            else:
                f=tokens.UsageTests();s=f.make([[(None,'date',1,2,3,4)]]);operation=lambda:s.conversations()
            s.guard=s._guard=guard
            self.assertTrue(operation());self.assertFalse(active[0])
