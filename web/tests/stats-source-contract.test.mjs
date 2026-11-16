import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';

const root = new URL('../../', import.meta.url);
const read = path => readFile(new URL(path, root), 'utf8');

test('Stats type follows actual Go response, not obsolete mock-only fields', async () => {
  const go = await read('server/server.go');
  const handler = go.slice(go.indexOf('func (s *Server) stats('), go.indexOf('func (s *Server) listTasks('));
  assert.ok(handler.length > 100);
  const types = await read('web/src/lib/types.ts');
  const stats = types.match(/export interface Stats \{([\s\S]*?)\n\}/)?.[1];
  assert.ok(stats);
  assert.doesNotMatch(handler, /roe_enabled|findings_confirmed/);
  assert.doesNotMatch(stats, /roe_enabled|findings_confirmed/);
  for (const [key, type] of [['assets', 'number'], ['engine_mode', 'EngineMode'], ['llm_configured', 'boolean']]) {
    assert.match(stats, new RegExp(`\\b${key}: ${type};`));
    assert.ok(handler.includes(`out["${key}"]`));
  }
  assert.match(stats, /active_task\?: Partial<Task>/);
  assert.ok(handler.includes('out["active_task"]'));
});

test('obsolete stats fields have no frontend consumers or mock producers', async () => {
  async function visit(url) {
    for (const entry of await readdir(url, { withFileTypes: true })) {
      const child = new URL(entry.name + (entry.isDirectory() ? '/' : ''), url);
      if (entry.isDirectory()) await visit(child);
      else if (/\.(ts|tsx|mjs)$/.test(entry.name)) {
        assert.doesNotMatch(await readFile(child, 'utf8'), /\b(?:roe_enabled|findings_confirmed)\b/, child.pathname);
      }
    }
  }
  await visit(new URL('web/src/', root));
});

test('Python stats does not fabricate absent Go capability fields', async () => {
  const source = await read('python/artex/server/stats.py');
  assert.doesNotMatch(source, /roe_enabled|findings_confirmed/);
  assert.match(source, /Dashboard dependency unavailable or invalid/);
});
