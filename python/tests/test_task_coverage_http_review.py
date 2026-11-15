"""Independent denial and callback-fence regressions for optional coverage HTTP."""
import unittest
import test_task_coverage_http as fixture
from test_task_coverage import PARENT

class Review(unittest.TestCase):
    def test_encoded_duplicate_query_rejected_before_resolution(self):
        f=fixture.Tests();f.make([])
        self.assertEqual(f.call('asset-refs','?asset_id=3&asset%5fid=4').status,400)
        self.assertEqual(f.resolve_calls,[])

    def test_current_callback_cannot_replace_original_policy(self):
        f=fixture.Tests();f.make([])
        def live(store):
            store.authorize=lambda *args:True
            return True
        f.service.current=live
        self.assertEqual(f.call().status,503)
        self.assertEqual(f.queries(),[])

    def test_authorization_requires_literal_true(self):
        f=fixture.Tests();f.make([PARENT],authorize=lambda *args:1)
        self.assertEqual(f.call().status,403)
        self.assertEqual(f.queries(),[])

if __name__=='__main__':unittest.main()
