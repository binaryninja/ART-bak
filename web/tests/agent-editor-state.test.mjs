import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';
import { ApiError } from '../src/lib/mutation-contract.mjs';

const root = new URL('../../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');
const source = await read('web/src/components/agent-editor.tsx');
function section(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `missing source boundary: ${start}`);
  return source.slice(from, to);
}
const reloadCode = section('  const reload = React.useCallback(', '\n  React.useEffect(');
const draftCode = section('  const updateResetDraft =', '\n  const [mcpVisible');
const lifecycleCode = section('  React.useEffect(() => {\n    editorActive.current', '\n\n  React.useEffect(');
const resetCode = [
  section('  async function resetPrompt()', '  async function saveWrapup()'),
  section('  async function resetWrapup()', '  async function saveTaskTimeoutWrapup()'),
  section('  async function resetTaskTimeoutWrapup()', '  async function saveConfig()'),
].join('\n');
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function detail(key = 'alpha', extra = {}) {
  return {
    agent: { id: key === 'alpha' ? '7' : '8', key, max_turns: 12, run_seconds: 300,
      llm_profile_id: 4, web_search: true, interactive_shell: false },
    prompt: `${key} prompt`, variables: [{ name: 'Now' }], versions: [{ version: 1 }],
    visibility: { mcp: [1], skill: ['lookup'] }, llm_profiles: [{ id: 4 }],
    wrapup_prompt: 'saved wrapup', wrapup_max_turns: 2, wrapup_default: 'code default',
    task_timeout_wrapup_prompt: 'saved timeout', task_timeout_wrapup_max_turns: 3,
    task_timeout_wrapup_supported: true, task_timeout_wrapup_default: 'code timeout',
    ...extra,
  };
}

// Exact declarations execute with inert deferred APIs and simple state setters.
// This models control flow, not React rendering, effect scheduling or type checks.
function harness(agentKey = 'alpha') {
  const reads = [], resets = [], changes = [], messages = [];
  const state = { prompt: '', wrapup: '', wrapupTurns: '0', ttWrapup: '', ttTurns: '0', loaded: false };
  const context = vm.createContext({
    ApiError, agentKey, editorActive: { current: true }, detailRequest: { current: null },
    hydratedAgentID: { current: null }, pendingResets: { current: new Map() }, resetDrafts: { current: {} },
    React: { useCallback: fn => fn, useEffect: fn => { context.cleanup = fn(); } },
    api: {
      getAgent(key) { const d = deferred(); reads.push({ key, ...d }); return d.promise; },
      ...Object.fromEntries(['resetAgentPrompt', 'resetAgentWrapup', 'resetAgentTaskTimeoutWrapup'].map(name =>
        [name, key => { const d = deferred(); resets.push({ name, key, ...d }); return d.promise; }])),
    },
    toast: { success: value => messages.push(['success', value]), error: value => messages.push(['error', value]) },
  });
  const draftFields = ['prompt', 'wrapup', 'wrapupTurns', 'ttWrapup', 'ttTurns'];
  context.resetDraftState = Object.fromEntries(draftFields.map(k => [k, state[k]]));
  context.resetDrafts.current = context.resetDraftState;
  for (const match of (reloadCode + resetCode).matchAll(/\b(set[A-Z]\w*)\(/g)) {
    const name = match[1], field = name[3].toLowerCase() + name.slice(4);
    context[name] = value => { state[field] = value; changes.push([field, value]); };
  }
  context.setResetDraftState = value => { Object.assign(state, value); changes.push(['draft', value]); };
  vm.runInContext(stripTypeScriptTypes(`${draftCode}\nglobalThis.editDraft = updateResetDraft;\n${lifecycleCode}\n${reloadCode}\nglobalThis.reload = reload;
    globalThis.resetActions = function(prompt, wrapup, wrapupTurns, ttWrapup, ttTurns) {
      ${resetCode}; return { resetPrompt, resetWrapup, resetTaskTimeoutWrapup };
    };`), context);
  return {
    context, state, reads, resets, changes, messages, reload: context.reload,
    actions: () => context.resetActions(...['prompt', 'wrapup', 'wrapupTurns', 'ttWrapup', 'ttTurns'].map(k => state[k])),
    edit(fields) { for (const [key, value] of Object.entries(fields)) { if (draftFields.includes(key)) context.editDraft(key, value); else state[key] = value; } },
    async hydrate() { context.reload(); reads.at(-1).resolve(detail(agentKey)); await flush(); },
    unmount: () => context.cleanup(),
  };
}

test('public editor keys the entire draft session; loading/error branches precede editable tabs', () => {
  assert.match(section('export function AgentEditor(', '\nfunction AgentEditorSession('), /<AgentEditorSession key=\{agentKey\} agentKey=\{agentKey\}/);
  const loading = source.indexOf('  if (!loaded || (!detail && refreshing))');
  const error = source.indexOf('  if (!detail)', loading);
  const tabs = source.indexOf('    <Tabs defaultValue="prompt"', error);
  assert.ok(loading > 0 && loading < error && error < tabs);
  assert.match(source.slice(loading, tabs), /role="status"/);
  assert.match(source.slice(error, tabs), /role="alert"/);
  assert.match(source.slice(error, tabs), /onClick=\{\(\) => reload\(\)\}>Retry/);
});
test('actual draft setter fences same-turn edits and applies functional updates before any render', async () => {
  const h = harness(); await h.hydrate(); const pending = h.actions().resetPrompt();
  h.resets[0].resolve({ version: 2 }); await pending;
  h.edit({ prompt: previous => previous + ' newer edit' });
  assert.equal(h.context.resetDrafts.current.prompt, 'alpha prompt newer edit');
  h.reads[1].resolve(detail('alpha', { prompt: 'reset default' })); await flush();
  assert.equal(h.state.prompt, 'alpha prompt newer edit');
});
test('first matching read hydrates saved form values, metadata and string identity', async () => {
  const h = harness(); h.reload(); assert.equal(h.state.loaded, false); assert.equal(h.state.refreshing, true);
  h.reads[0].resolve(detail()); await flush();
  assert.equal(h.state.prompt, 'alpha prompt'); assert.equal(h.state.maxTurns, '12'); assert.equal(h.state.runSecs, '300');
  assert.equal(h.state.llmProfileId, '4'); assert.deepEqual(h.state.mcpVisible, [1]); assert.deepEqual(h.state.skillVisible, ['lookup']);
  assert.equal(h.context.hydratedAgentID.current, '7'); assert.equal(h.state.loaded, true); assert.equal(h.state.refreshing, false);
});
test('newer read wins; old success and old failure cannot replace current fields or finish loading', async () => {
  for (const oldFails of [false, true]) {
    const h = harness(); h.reload(); const old = h.reads[0]; h.reload(); const currentToken = h.context.detailRequest.current;
    if (oldFails) old.reject(new ApiError(404, {}, 'missing')); else old.resolve(detail('alpha', { prompt: 'stale' }));
    await flush(); assert.equal(h.context.detailRequest.current, currentToken); assert.equal(h.state.loaded, false);
    h.reads[1].resolve(detail('alpha', { prompt: 'newest' })); await flush(); assert.equal(h.state.prompt, 'newest');
  }
});
test('late success after a newer success cannot revert the draft or server history', async () => {
  const h = harness(); h.reload(); h.reload();
  h.reads[1].resolve(detail('alpha', { prompt: 'new', versions: [{ version: 2 }] })); await flush();
  h.reads[0].resolve(detail('alpha', { prompt: 'old', versions: [{ version: 1 }] })); await flush();
  assert.equal(h.state.prompt, 'new'); assert.equal(h.state.versions[0].version, 2);
});
test('unmounted A response cannot publish into B, and late reload cannot start a request', async () => {
  for (const fails of [false, true]) {
    const a = harness('alpha'); a.reload(); a.unmount(); const changes = a.changes.length;
    const b = harness('beta'); await b.hydrate();
    if (fails) a.reads[0].reject(new Error('offline')); else a.reads[0].resolve(detail('alpha'));
    await flush(); a.reload();
    assert.equal(a.changes.length, changes); assert.equal(a.reads.length, 1);
    assert.equal(b.state.prompt, 'beta prompt'); assert.equal(b.state.detail.agent.key, 'beta');
  }
});
test('wrong key, missing or non-string identity never hydrates an editable form', async () => {
  for (const agent of [{ id: '7', key: 'beta' }, { key: 'alpha' }, { id: 7, key: 'alpha' }, { id: '', key: 'alpha' }]) {
    const h = harness(); h.reload(); h.reads[0].resolve(detail('alpha', { agent })); await flush();
    assert.equal(h.context.hydratedAgentID.current, null); assert.equal(h.state.detail, null); assert.equal(h.state.prompt, '');
    assert.match(h.state.loadError, /Unable to load/);
  }
});
test('agent deleted and recreated under the same key cannot inherit the old draft identity', async () => {
  const h = harness(); await h.hydrate(); h.edit({ prompt: 'unsaved old identity' }); h.reload();
  h.reads[1].resolve(detail('alpha', { agent: { ...detail().agent, id: '9' } })); await flush();
  assert.equal(h.state.detail, null); assert.equal(h.state.prompt, 'unsaved old identity');
  assert.equal(h.context.hydratedAgentID.current, '7'); assert.match(h.state.loadError, /Close and reopen/);
});
test('only actual404 is labeled not found;503/network failure remains retryable read error', async () => {
  for (const [error, missing] of [[new ApiError(404, {}, 'missing'), true], [new ApiError(503, {}, 'unavailable'), false], [new Error('offline'), false]]) {
    const h = harness(); h.reload(); h.reads[0].reject(error); await flush();
    assert.equal(h.state.loadError.startsWith('Agent not found:'), missing); assert.equal(h.state.detail, null);
    h.reload(); assert.equal(h.state.refreshing, true); h.reads[1].resolve(detail()); await flush();
    assert.equal(h.state.loadError, null); assert.equal(h.state.prompt, 'alpha prompt');
  }
});
test('refresh updates metadata/history without overwriting any editable agent draft', async () => {
  const h = harness(); await h.hydrate();
  const edits = { prompt: 'new prompt', wrapup: 'new wrapup', wrapupTurns: '9', ttWrapup: 'new timeout', ttTurns: '8',
    maxTurns: '99', runSecs: '999', llmProfileId: '12', webSearch: false, interactiveShell: true,
    mcpVisible: [8], skillVisible: ['new-skill'] };
  h.reload(); h.edit(edits);
  h.reads[1].resolve(detail('alpha', { versions: [{ version: 5 }], wrapup_default: 'new code default' })); await flush();
  for (const [key, value] of Object.entries(edits)) assert.deepEqual(h.state[key], value, key);
  assert.equal(h.state.versions[0].version, 5); assert.equal(h.state.wrapupDefault, 'new code default');
});
test('read failure and retry keep unsaved drafts rather than hydrating defaults again', async () => {
  const h = harness(); await h.hydrate(); h.edit({ prompt: 'unsaved' }); h.reload();
  h.reads[1].reject(new ApiError(503, {}, 'unavailable')); await flush(); assert.equal(h.state.detail, null);
  h.reload(); h.reads[2].resolve(detail()); await flush(); assert.equal(h.state.prompt, 'unsaved');
});
test('explicit reset replaces only its own unchanged fields after its acknowledged write and read', async () => {
  const cases = [
    ['resetPrompt', { prompt: 'unsaved prompt' }, { prompt: 'reset prompt' }, ['prompt']],
    ['resetWrapup', { wrapup: 'unsaved wrapup', wrapupTurns: '9' }, { wrapup_prompt: '', wrapup_max_turns: 0 }, ['wrapup', 'wrapupTurns']],
    ['resetTaskTimeoutWrapup', { ttWrapup: 'unsaved timeout', ttTurns: '8' }, { task_timeout_wrapup_prompt: '', task_timeout_wrapup_max_turns: 0 }, ['ttWrapup', 'ttTurns']],
  ];
  for (const [action, edits, result, affected] of cases) {
    const h = harness(); await h.hydrate(); h.edit(edits); const before = { ...h.state };
    const pending = h.actions()[action](); h.resets[0].resolve({ version: 2 }); await pending;
    h.reads[1].resolve(detail('alpha', result)); await flush();
    for (const key of ['prompt', 'wrapup', 'wrapupTurns', 'ttWrapup', 'ttTurns']) {
      if (affected.includes(key)) assert.notEqual(h.state[key], before[key], key); else assert.equal(h.state[key], before[key], key);
    }
    assert.equal(h.context.pendingResets.current.size, 0);
  }
});
test('typing during reset mutation or subsequent read preserves the newer draft', async () => {
  for (const phase of ['mutation', 'read']) {
    const h = harness(); await h.hydrate(); const pending = h.actions().resetPrompt();
    if (phase === 'mutation') h.edit({ prompt: 'newer edit' });
    h.resets[0].resolve({ version: 2 }); await pending;
    if (phase === 'read') h.edit({ prompt: 'newer edit' });
    h.reads[1].resolve(detail('alpha', { prompt: 'reset default' })); await flush();
    assert.equal(h.state.prompt, 'newer edit');
  }
});
test('editing either wrapup field prevents partial reset of its paired draft', async () => {
  for (const [action, edits, result] of [
    ['resetWrapup', { wrapupTurns: '77' }, { wrapup_prompt: '', wrapup_max_turns: 0 }],
    ['resetTaskTimeoutWrapup', { ttWrapup: 'new timeout' }, { task_timeout_wrapup_prompt: '', task_timeout_wrapup_max_turns: 0 }],
  ]) {
    const h = harness(); await h.hydrate(); const pending = h.actions()[action](); h.edit(edits); const draft = { ...h.state };
    h.resets[0].resolve({}); await pending; h.reads[1].resolve(detail('alpha', result)); await flush();
    for (const key of ['wrapup', 'wrapupTurns', 'ttWrapup', 'ttTurns']) assert.equal(h.state[key], draft[key]);
  }
});
test('concurrent acknowledged resets survive a superseding refresh and stale response', async () => {
  const h = harness(); await h.hydrate(); const actions = h.actions();
  const prompt = actions.resetPrompt(), wrapup = actions.resetWrapup();
  h.resets[0].resolve({ version: 2 }); await prompt; h.resets[1].resolve({}); await wrapup;
  h.reload(); // A separate save's metadata refresh supersedes both reset reads.
  h.reads[3].resolve(detail('alpha', { prompt: 'reset prompt', wrapup_prompt: '', wrapup_max_turns: 0 })); await flush();
  h.reads[1].resolve(detail()); h.reads[2].resolve(detail()); await flush();
  assert.equal(h.state.prompt, 'reset prompt'); assert.equal(h.state.wrapup, ''); assert.equal(h.state.wrapupTurns, '0');
  assert.equal(h.context.pendingResets.current.size, 0);
});
test('failed reset does not queue replacement; failed reset-read can be retried without losing edits', async () => {
  const h = harness(); await h.hydrate(); const failed = h.actions().resetPrompt();
  h.resets[0].reject(new Error('unsupported')); await failed;
  assert.equal(h.reads.length, 1); assert.equal(h.context.pendingResets.current.size, 0);
  const reset = h.actions().resetPrompt(); h.resets[1].resolve({ version: 2 }); await reset;
  h.reads[1].reject(new ApiError(503, {}, 'unavailable')); await flush();
  h.edit({ prompt: 'keep after read error' }); h.reload(); h.reads[2].resolve(detail('alpha', { prompt: 'default' })); await flush();
  assert.equal(h.state.prompt, 'keep after read error'); assert.equal(h.context.pendingResets.current.size, 0);
});
test('actual Go and Python detail contracts retain string IDs and requested keys', async () => {
  const go = await read('server/server_mgmt.go');
  assert.match(go, /"agent": agentDTO\(a\), "prompt": cur, "variables": vars, "versions": vers/);
  const dto = await read('server/dto.go'); assert.match(dto, /ID:\s+i64s\(a.ID\),\s+Key:\s+a.Key/);
  const types = await read('web/src/lib/types.ts'); assert.match(types, /export interface Agent \{\s+id: string;\s+key: string/);
  const python = await read('python/artex/db/agents.py'); assert.match(python, /return dict\(id=str\(self.id\)/);
  assert.match(python, /names=\('key','name'/);
  const routes = await read('python/artex/server/agents.py');
  assert.match(routes, /agent=agent\.to_dict\(\)/); assert.match(routes, /AgentUnavailable:return error_response\(503/);
  assert.match(routes, /AgentMissing:return error_response\(404/);
});
