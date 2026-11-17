import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';
import { ApiError, confirmedTaskControl, mutationMustWait } from '../src/lib/mutation-contract.mjs';

const root = new URL('../../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');
const apiSource = await read('web/src/lib/api.ts');
const view = await read('web/src/app/(main)/function/tasks/detail/page.tsx');
function section(source, start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `source boundary missing: ${start}`);
  return source.slice(from, to);
}
const pause = (id = '17') => ({ id, paused: true, queued: false, status: 'paused' });
const resume = (id = '17', queued = false) => ({ id, paused: false, queued, status: queued ? 'queued' : 'running' });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

// Run the actual transport and method declarations without loading React/Next.
// These are source-derived control-flow tests, not a TS compile or browser test.
function transport(response) {
  const calls = [];
  const http = section(apiSource, 'export async function http<', '\n// sseUrl').replace('export async', 'async');
  const helpers = section(apiSource, 'const get = <T>', '\n// Go serializes');
  const method = section(apiSource, '  controlTask:', '  controlTasksBatch:');
  const chat = section(apiSource, '  chat: (', '  chatStatus:');
  const context = vm.createContext({
    ApiError, confirmedTaskControl, MOCK: false, getToken: () => 'synthetic-token',
    tq: (id) => id ? `?task=${id}` : '',
    fetch: async (...args) => {
      calls.push(args);
      if (response instanceof Error) throw response;
      return response;
    },
  });
  vm.runInContext(stripTypeScriptTypes(`${http}\n${helpers}\nglobalThis.api = {${method}${chat}};`), context);
  return { api: context.api, calls };
}
const response = (body, status = 200) => ({ status, ok: status >= 200 && status < 300, json: async () => body });

test('single-task receipts preserve paused/running/queued and ignore unrelated fields', () => {
  for (const [receipt, action] of [[pause(), 'pause'], [resume(), 'resume'], [resume('17', true), 'resume']]) {
    assert.deepEqual(confirmedTaskControl({ ...receipt, arbitrary: 'not copied' }, '17', action), receipt);
  }
});
test('wrong task, malformed or contradictory receipts never confirm a mutation', () => {
  const bad = [null, [], 'ok', {}, pause('18'), { ...pause(), id: 17 }, { ...pause(), paused: 1 },
    { ...pause(), queued: undefined }, { ...pause(), queued: true }, { ...pause(), status: 'running' },
    { ...pause(), pending: true }, { ...pause(), unknown: true }, { ...pause(), retry: false }, resume()];
  for (const receipt of bad) assert.throws(() => confirmedTaskControl(receipt, '17', 'pause'), mutationMustWait);
  for (const receipt of [pause(), { ...resume(), status: 'queued' }, { ...resume('17', true), status: 'running' }]) {
    assert.throws(() => confirmedTaskControl(receipt, '17', 'resume'), mutationMustWait);
  }
  assert.throws(() => confirmedTaskControl(pause(), '17', 'delete'), mutationMustWait);
});
test('actual API method sends once and confirms the exact synchronous HTTP200 receipt', async () => {
  const fixture = transport(response(resume('17', true)));
  assert.deepEqual(await fixture.api.controlTask('17', 'resume'), resume('17', true));
  assert.equal(fixture.calls.length, 1);
  const [url, options] = fixture.calls[0];
  assert.equal(url, '/api/tasks/17/control');
  assert.equal(options.method, 'POST');
  assert.equal(options.body, '{"action":"resume"}');
  assert.equal(options.headers.Authorization, 'Bearer synthetic-token');
});
test('202/204, unreadable success, mismatched identity and lost connection remain unconfirmed', async () => {
  for (const r of [response(pause(), 202), response(pause(), 204), response(pause('18')),
    { ...response(pause()), json: async () => { throw new SyntaxError('truncated'); } }, new Error('offline')]) {
    const fixture = transport(r);
    await assert.rejects(fixture.api.controlTask('17', 'pause'), mutationMustWait);
    assert.equal(fixture.calls.length, 1);
  }
});
test('known HTTP conflict stays known and legitimate chat HTTP202 still succeeds', async () => {
  await assert.rejects(transport(response({ error: 'Not paused' }, 409)).api.controlTask('17', 'resume'),
    error => error.status === 409 && !mutationMustWait(error));
  assert.deepEqual(await transport(response({ status: 'accepted' }, 202)).api.chat('hello', '17'), { status: 'accepted' });
});

function detailHarness() {
  const controls = [], archives = [], reads = [], messages = [], updates = [], loads = [];
  const context = vm.createContext({
    id: '17', task: { id: '17', status: 'running', paused: false, name: 'existing' }, paused: false, archiving: false,
    currentTaskID: { current: '17' }, viewActive: { current: true }, archiveRequested: { current: false },
    controlRequests: { current: new Map() }, uncertainControls: { current: new Set() }, taskLoadInFlight: { current: null },
    mutationMustWait, ApiError, React: { useCallback: (fn) => fn },
    readFeedback: { id: '17', loading: false, error: null, notFound: false },
    api: {
      controlTask(id, action) { const d = deferred(); controls.push({ id, action, ...d }); return d.promise; },
      archiveTask(id) { const d = deferred(); archives.push({ id, ...d }); updates.push(['archive', id]); return d.promise; },
      task(id) { const d = deferred(); reads.push({ id, ...d }); return d.promise; },
      stats: () => Promise.resolve(null),
    },
    toast: { success: (text) => messages.push(['success', text]), error: (text) => messages.push(['error', text]) },
    router: { push: (url) => updates.push(['navigate', url]) },
    setTask(value) { context.task = typeof value === 'function' ? value(context.task) : value; updates.push(['task']); },
    setPaused(value) { context.paused = value; updates.push(['paused', value]); },
    setReadFeedback(value) { context.readFeedback = typeof value === 'function' ? value(context.readFeedback) : value; },
    setControllingIDs(value) { context.controllingIDs = value; updates.push(['pending']); },
    setUncertainControlIDs(value) { context.uncertainControlIDs = value; updates.push(['uncertain']); },
    setArchiving(value) { context.archiving = value; updates.push(['archiving']); },
    load() { loads.push(context.currentTaskID.current); },
  });
  const controlsCode = section(view, '  async function togglePause()', '\n  if (!task || task.id !== id)');
  const pollCode = section(view, '  const load = React.useCallback(', '\n  React.useEffect(');
  // Factory parameters imitate a render's captured props/state; refs remain shared.
  vm.runInContext(stripTypeScriptTypes(`globalThis.actions = function(id, task, paused, archiving, load) {
    ${controlsCode}; return { togglePause, archiveTask };
  }; globalThis.poll = function(id) { ${pollCode}; return load; };`), context);
  return {
    context, controls, archives, reads, messages, updates, loads,
    actions: () => context.actions(context.id, context.task, context.paused, context.archiving, context.load),
    poll: (id = context.id) => context.poll(id),
    navigate(id) { context.id = id; context.currentTaskID.current = id; context.task = { id, status: 'running', paused: false }; context.paused = false; },
  };
}

test('detail blocks repeated clicks and archive while pending, renders queued receipt honestly', async () => {
  const h = detailHarness(); h.context.paused = true;
  const action = h.actions();
  const first = action.togglePause(); await action.togglePause(); await action.archiveTask();
  assert.equal(h.controls.length, 1); assert.equal(h.controls[0].action, 'resume');
  assert.equal(h.updates.some(([name]) => name === 'archive'), false);
  h.controls[0].resolve(resume('17', true)); await first;
  assert.equal(h.context.task.status, 'queued'); assert.equal(h.context.task.queued, true);
  assert.equal(h.context.task.name, 'existing'); assert.equal(h.context.paused, false);
  assert.deepEqual(h.messages, [['success', 'Task queued for resumption']]);
  assert.equal(h.context.controlRequests.current.size, 0); assert.deepEqual(h.loads, ['17']);
});
test('archive-first same-render actions cannot dispatch control or a duplicate archive', async () => {
  const h = detailHarness(); h.context.paused = true;
  const actions = h.actions(); const archive = actions.archiveTask();
  await actions.togglePause(); await actions.archiveTask();
  assert.equal(h.archives.length, 1); assert.equal(h.controls.length, 0);
  h.archives[0].resolve({ status: 'pending' }); await archive;
  // A successful enqueue remains gated until router navigation commits.
  await actions.togglePause(); await actions.archiveTask();
  assert.equal(h.archives.length, 1); assert.equal(h.controls.length, 0);
  assert.equal(h.context.archiveRequested.current, true);
  assert.ok(h.updates.some(([kind]) => kind === 'navigate'));
});
test('archive failure releases its immediate gate, preserving existing failure behavior', async () => {
  const h = detailHarness(); h.context.paused = true;
  const actions = h.actions(); const archive = actions.archiveTask();
  h.archives[0].reject(new Error('archive unavailable')); await archive;
  assert.equal(h.context.archiveRequested.current, false); assert.equal(h.context.archiving, false);
  const control = h.actions().togglePause(); assert.equal(h.controls.length, 1);
  h.controls[0].resolve(resume()); await control;
});
test('pause receipt, not an inverted stale boolean, supplies confirmed state', async () => {
  const h = detailHarness(); const pending = h.actions().togglePause();
  h.context.paused = true; h.controls[0].resolve(pause()); await pending;
  assert.equal(h.context.paused, true); assert.equal(h.context.task.status, 'paused');
  assert.deepEqual(h.messages, [['success', 'Exploration paused']]);
});
test('stale A completion cannot replace B or clear its pending control/poll', async () => {
  const h = detailHarness(); const a = h.actions().togglePause();
  h.navigate('18'); const b = h.actions().togglePause(); const bPoll = { id: '18' }; h.context.taskLoadInFlight.current = bPoll;
  h.controls[0].resolve(pause()); await a;
  assert.equal(h.context.task.id, '18'); assert.equal(h.context.paused, false);
  assert.equal(h.context.controlRequests.current.has('18'), true);
  assert.equal(h.context.taskLoadInFlight.current, bPoll); assert.deepEqual(h.messages, []); assert.deepEqual(h.loads, []);
  h.controls[1].resolve(pause('18')); await b;
  assert.equal(h.context.paused, true); assert.equal(h.context.controlRequests.current.size, 0);
});
test('A to B to A cannot repeat A while its original control is pending', async () => {
  const h = detailHarness(); const a = h.actions().togglePause();
  h.navigate('18'); h.navigate('17'); await h.actions().togglePause();
  assert.equal(h.controls.length, 1); h.controls[0].resolve(pause()); await a;
  assert.equal(h.context.task.status, 'paused');
});
test('uncertain failures latch per task through navigation and successful polling without invented success', async () => {
  for (const payload of [{ unknown: true }, { pending: true }, { retry: false }]) {
    const h = detailHarness(); const pending = h.actions().togglePause();
    h.controls[0].reject(new ApiError(503, payload, 'unconfirmed')); await pending;
    h.poll()(); h.reads[0].resolve(pause()); await flush();
    h.navigate('18'); h.navigate('17'); await h.actions().togglePause(); await h.actions().archiveTask();
    assert.equal(h.controls.length, 1); assert.equal(h.context.uncertainControls.current.has('17'), true);
    assert.equal(h.messages.some(([kind]) => kind === 'success'), false);
    assert.equal(h.updates.some(([name]) => name === 'archive'), false);
  }
});
test('known failure unlocks without changing the previous task state', async () => {
  const h = detailHarness(); const pending = h.actions().togglePause();
  h.controls[0].reject(new ApiError(409, { error: 'Conflict' }, '')); await pending;
  assert.equal(h.context.paused, false); assert.equal(h.context.task.status, 'running');
  assert.equal(h.context.uncertainControls.current.size, 0); assert.equal(h.context.controlRequests.current.size, 0);
  const retry = h.actions().togglePause(); assert.equal(h.controls.length, 2);
  h.controls[1].resolve(pause()); await retry;
});
test('unmounted control completion produces no state, toast or follow-up read', async () => {
  for (const fails of [false, true]) {
    const h = detailHarness(); const pending = h.actions().togglePause(); h.context.viewActive.current = false;
    const before = h.updates.length;
    if (fails) h.controls[0].reject(new ApiError(0, { unknown: true }, 'lost'));
    else h.controls[0].resolve(pause());
    await pending;
    assert.equal(h.updates.length, before); assert.deepEqual(h.messages, []); assert.deepEqual(h.loads, []);
  }
});
test('missing, different, terminal or archiving task cannot issue control', async () => {
  for (const task of [null, { id: '18', status: 'running' }, ...['done', 'failed', 'timeout'].map(status => ({ id: '17', status }))]) {
    const h = detailHarness(); h.context.task = task; await h.actions().togglePause(); assert.equal(h.controls.length, 0);
  }
  const h = detailHarness(); h.context.archiving = true; await h.actions().togglePause(); assert.equal(h.controls.length, 0);
});
test('poll request identity prevents older A from replacing newer A after A to B to A', async () => {
  const h = detailHarness(); h.poll()(); h.navigate('18'); h.poll()(); h.navigate('17'); h.poll()();
  h.reads[0].resolve({ ...pause(), name: 'stale' }); await flush();
  assert.notEqual(h.context.task.name, 'stale'); assert.equal(h.context.taskLoadInFlight.current.id, '17');
  h.reads[2].resolve({ ...pause(), name: 'new' }); await flush();
  h.reads[1].resolve({ ...resume('18'), name: 'wrong task' }); await flush();
  assert.equal(h.context.task.name, 'new'); assert.equal(h.context.taskLoadInFlight.current, null);
});
test('polls begun before or during control cannot overwrite its confirmed receipt', async () => {
  const h = detailHarness(); h.poll()(); const pending = h.actions().togglePause(); h.poll()();
  h.controls[0].resolve(pause()); await pending;
  for (const read of h.reads) read.resolve(resume()); await flush();
  assert.equal(h.context.task.status, 'paused'); assert.equal(h.context.paused, true);
  assert.equal(h.context.taskLoadInFlight.current, null);
});
test('duplicate polls are coalesced and an unmounted poll cannot render', async () => {
  const h = detailHarness(); const load = h.poll(); load(); load(); assert.equal(h.reads.length, 1);
  h.context.viewActive.current = false; h.reads[0].resolve(pause()); await flush(); assert.deepEqual(h.updates, []);
});
test('obsolete handlers cannot initiate control, archive or polling after navigation or unmount', async () => {
  for (const unmount of [false, true]) {
    const h = detailHarness(); const actions = h.actions(); const poll = h.poll();
    if (unmount) h.context.viewActive.current = false;
    else h.navigate('18');
    await actions.togglePause(); await actions.archiveTask(); poll();
    assert.equal(h.controls.length, 0); assert.equal(h.reads.length, 0); assert.deepEqual(h.updates, []);
  }
});
test('actual view connects pending/uncertain state to disabled buttons', () => {
  assert.match(view, /disabled=\{terminal \|\| controlUnavailable\}/);
  assert.match(view, /const controlUnavailable = task\.id !== id \|\| controlling \|\| controlUncertain \|\| archiving/);
  assert.match(view, /const canArchive = archiveLifecycleEligible && !task\.archive_blocked_by_task_id && !controlUnavailable/);
  assert.match(view, /controlLabel = "Unconfirmed"/);
});
test('receipts and statuses are tied to current Go/Python producers, not a speculative contract', async () => {
  const go = await read('server/task_control.go');
  const handler = await read('server/server.go');
  const python = await read('python/artex/manager.py');
  const routes = await read('python/artex/server/tasks.py');
  for (const key of ['id', 'paused', 'queued', 'status']) {
    assert.ok(section(go, 'type taskControlResult struct', '\n}').includes(`json:"${key}"`));
  }
  assert.match(go, /out\.Paused, out\.Status = true, "paused"/);
  assert.match(go, /out\.Status = map\[bool\]string\{true: "queued", false: "running"\}\[queued\]/);
  assert.match(section(handler, 'func (s *Server) control(', '\n// controlIntent'), /writeJSON\(w, 200, result\)/);
  assert.match(routes, /result = self\.manager\.control_task\(params\['id'\], command\)\s+return json_response\(200, dict\(result\)\)/);
  assert.match(python, /return \{'id': key, 'paused': True, 'queued': False, 'status': 'paused'\}/);
  assert.match(python, /return \{'id': key, 'paused': False, 'queued': False, 'status': 'running'\}/);
  assert.match(await read('python/artex/server/chat.py'), /json_response\(202,\{'status':'accepted'\}\)/);
});
