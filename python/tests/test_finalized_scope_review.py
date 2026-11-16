"""Independent exact scope typing check over temporary synthetic transcripts."""
import unittest
import test_finalized_scoped_result as fixture
from artex.transcripts import TranscriptConflict
class Review(unittest.TestCase):
    def test_numeric_scope_type_is_not_coerced_on_replay(self):
        f=fixture.ScopedReceiptTests();f.setUp();self.addCleanup(f.doCleanups)
        turn,_=f.finalized()
        with self.assertRaises(TranscriptConflict):
            f.adapter.finalized_scoped_result('s',turn.id,dict(fixture.SCOPE,task_id=5.0))
