"""Static source contracts only; no browser or application execution."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]/'web/src/app/(main)'
class FrontendInterceptCASReview(unittest.TestCase):
    def test_mutation_errors_refresh_rows_without_retry_or_new_edit_version(self):
        cases=(('system/intercept/assets/page.tsx',('handleSave','handleDelete','handleToggle')),
               ('function/tasks/detail/_tabs/overview-tab.tsx',('saveEdit','remove','toggle')))
        for path,names in cases:
            source=(ROOT/path).read_text()
            for name in names:
                with self.subTest(path=path,handler=name):
                    body=source.split('  async function '+name+'(',1)[1].split('\n  }',1)[0]
                    catch=body.split('} catch (e) {',1)[1].split('} finally',1)[0]
                    self.assertIn('load()',catch)
                    self.assertNotIn('api.',catch)
                    self.assertNotIn('setEditUpdatedAt',catch)
                    self.assertNotIn('setEditing',catch)
