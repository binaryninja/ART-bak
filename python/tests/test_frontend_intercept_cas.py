"""Source contract only; no frontend app code, npm scripts or browser execution."""
from pathlib import Path
import re,unittest
ROOT=Path(__file__).resolve().parents[2]
class FrontendInterceptCASTests(unittest.TestCase):
    def test_six_mutators_require_exact_version_header(self):
        api=(ROOT/'web/src/lib/api.ts').read_text()
        for name in ('updateTaskInterceptRule','deleteTaskInterceptRule','toggleTaskInterceptRule','updateAssetInterceptRule','deleteAssetInterceptRule','toggleAssetInterceptRule'):
            block=api[api.index('  '+name+':'):].split('\n\n',1)[0].split('\n  //',1)[0]
            # Each next named API declaration delimits the current arrow property.
            block=re.split(r'\n  [A-Za-z]+:',block,maxsplit=1)[0]
            self.assertIn('updatedAt: string',block);self.assertIn('headers: interceptVersion(updatedAt)',block)
        # The six API methods retain their alias, now backed by the shared pure
        # helper used by global tool-rule mutations too. Follow that exact import
        # and alias rather than assuming the helper is declared inline.
        self.assertRegex(api,r'import \{[^}]*\bruleVersionHeaders\b[^}]*\} from \"\./mutation-contract\.mjs\";')
        self.assertIn('const interceptVersion = ruleVersionHeaders;',api)
        source=(ROOT/'web/src/lib/mutation-contract.mjs').read_text()
        helper=source.split('export function ruleVersionHeaders(version) {',1)[1].split('\nexport function ',1)[0]
        self.assertIn("'X-Rule-Updated-At': version",helper)
        self.assertIn("typeof version !== 'string'",helper)
        self.assertIn('.test(version)',helper)
        self.assertNotIn('Date.',helper);self.assertNotIn('new Date',helper)
        self.assertIn('Reload the rules before editing',helper)
    def test_create_remains_header_free(self):
        api=(ROOT/'web/src/lib/api.ts').read_text()
        for name in ('createTaskInterceptRule','createAssetInterceptRule'):
            block=api[api.index('  '+name+':'):].split('\n',2)[:2]
            self.assertNotIn('interceptVersion','\n'.join(block));self.assertNotIn('updatedAt','\n'.join(block))
    def test_editor_snapshot_and_mutation_row_versions(self):
        task=(ROOT/'web/src/app/(main)/function/tasks/detail/_tabs/overview-tab.tsx').read_text()
        self.assertIn('setEditUpdatedAt(r.updated_at)',task);self.assertIn('}, editUpdatedAt)',task)
        self.assertIn('deleteTaskInterceptRule(taskId, r.id, r.updated_at)',task)
        self.assertIn('toggleTaskInterceptRule(taskId, r.id, !r.enabled, r.updated_at)',task)
        global_ui=(ROOT/'web/src/app/(main)/system/intercept/assets/page.tsx').read_text()
        self.assertIn('editing.id, payload, editing.updated_at',global_ui)
        self.assertIn('deleteAssetInterceptRule(rule.id, rule.updated_at)',global_ui)
        self.assertIn('toggleAssetInterceptRule(rule.id, !rule.enabled, rule.updated_at)',global_ui)
