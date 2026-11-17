import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';
import { ApiError, confirmedIntentState, mutationMustWait } from '../src/lib/mutation-contract.mjs';

const source = await readFile(new URL('../src/app/(main)/function/tasks/detail/_tabs/sessions-tab.tsx', import.meta.url), 'utf8');
function section(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `missing source boundary: ${start}`);
  return source.slice(from, to);
}
const controlCode = section('  const controlWorker = React.useCallback(', '\n  // SSE connection state');
const patchCode = section('  const patchIntentState = React.useCallback(', '\n  const controlWorker');
const firstCode = section('  // ── worker (intent) session list', '\n  const loadOlderIntents');
const olderCode = section('  const loadOlderIntents = React.useCallback(', '\n  // Combined, de-duplicated worker list');
const lifecycleCode = section('  React.useEffect(() => {\n    workerViewAlive.current', '\n  const [cancelReason');
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const row = (id = '7', state = 'running') => ({ id, state });
const page = (items = [row()], hasMore = false) => ({ items, hasMore });
const session = (id = '7') => ({ intent_id: id, id, role: 'worker', inherited: false });

// Exact source declarations with inert APIs and minimal state setters. Not a
// mounted React tree, effect scheduler, TypeScript compile or browser acceptance.
function harness() {
  const controls = [], reads = [], messages = [], changes = [], intervals = [];
  const state = { intents: [row()], olderIntents: [], loadingOlderIntents: false, cancelReason: '' };
  const context = vm.createContext({
    ApiError, confirmedIntentState, mutationMustWait, PYTHON_PROFILE: false,
    workerViewTask: { current: 'A' }, workerViewAlive: { current: true },
    controlRequests: { current: new Map() }, uncertainControls: { current: new Set() },
    workerEpochs: { current: new Map() }, firstWorkerRead: { current: null }, olderWorkerRead: { current: null },
    reloadWorkers: { current: null }, firstIntentsRef: { current: { taskId: 'A', items: state.intents } }, cancelTarget: { current: null },
    React: { useCallback: fn => fn, useEffect: fn => { context.lastCleanup = fn(); } },
    api: {
      controlIntent(...args) { const d = deferred(); controls.push({ args, ...d }); return d.promise; },
      intentsPage(...args) { const d = deferred(); reads.push({ args, ...d }); return d.promise; },
    },
    toast: { success: value => messages.push(['success', value]), error: value => messages.push(['error', value]) },
    setInterval: fn => { intervals.push(fn); return fn; }, clearInterval: () => {},
  });
  for (const match of (controlCode + patchCode + firstCode + olderCode).matchAll(/\b(set[A-Z]\w*)\(/g)) {
    const name = match[1], key = name[3].toLowerCase() + name.slice(4);
    if (name === 'setInterval') continue;
    context[name] = value => { state[key] = typeof value === 'function' ? value(state[key]) : value; changes.push(key); };
  }
  vm.runInContext(stripTypeScriptTypes(`${lifecycleCode}\nglobalThis.unmount = lastCleanup;\n${patchCode}
    globalThis.controlFor = function(taskId) { ${controlCode}; return controlWorker; };
    globalThis.olderFor = function(taskId, intents, olderIntents) { ${olderCode}; return loadOlderIntents; };
    globalThis.startPollFor = function(taskId) { ${firstCode}; return lastCleanup; };`), context);
  return {
    context, state, controls, reads, messages, changes, intervals,
    control: (id = context.workerViewTask.current) => context.controlFor(id),
    older: (id = context.workerViewTask.current) => context.olderFor(id, state.intents, state.olderIntents),
    poll: (id = context.workerViewTask.current) => context.startPollFor(id),
    navigate(id, rows = [row('8')]) { context.workerViewTask.current = id; state.intents = rows; state.olderIntents = []; context.firstIntentsRef.current = { taskId: id, items: rows }; },
    cancel(id = '7') { const target = { ...session(id), controlTaskId: context.workerViewTask.current }; context.cancelTarget.current = target; state.cancelIntent = target; state.cancelReason = 'original reason'; return target; },
    unmount: () => context.unmount(),
  };
}

test('same-render duplicate and competing worker controls issue only one request per task', async () => {
  const h = harness(), control = h.control(); const first = control(session(), 'pause');
  await control(session(), 'pause'); await control(session('8'), 'resume');
  assert.equal(h.controls.length, 1); assert.deepEqual(h.controls[0].args, ['A', '7', 'pause', undefined, undefined]);
  h.controls[0].resolve({ id: 7, state: 'paused' }); await first;
  assert.equal(h.context.controlRequests.current.size, 0); assert.equal(h.state.intents[0].state, 'paused');
  assert.equal(h.context.firstIntentsRef.current.items[0].state, 'paused'); assert.equal(h.context.workerEpochs.current.get('A'), 2);
});
test('A completion cannot patch B, clear B pending control or close B deletion dialog', async () => {
  const h = harness(); const a = h.control()(session(), 'pause'); h.navigate('B');
  const targetB = h.cancel('8'); const b = h.control()(session('8'), 'resume');
  h.controls[0].resolve({ id: 7, state: 'paused' }); await a;
  assert.equal(h.state.intents[0].id, '8'); assert.equal(h.state.intents[0].state, 'running');
  assert.equal(h.context.controlRequests.current.has('B'), true); assert.equal(h.state.controllingTasks.get('B'), '8');
  assert.equal(h.context.cancelTarget.current, targetB); assert.equal(h.state.cancelIntent, targetB); assert.deepEqual(h.messages, []);
  h.controls[1].resolve({ id: 8, state: 'open' }); await b; assert.equal(h.state.intents[0].state, 'open');
});
test('A to B to A retains original pending control and forbids replay before acknowledgement', async () => {
  const h = harness(); const a = h.control()(session(), 'pause');
  h.navigate('B'); h.navigate('A', [row()]); await h.control()(session(), 'pause');
  assert.equal(h.controls.length, 1); h.controls[0].resolve({ id: 7, state: 'paused' }); await a;
  assert.equal(h.state.intents[0].state, 'paused');
});
test('returning to A before its new rows load cannot patch the still-rendered B cache', async () => {
  const h = harness(); const pending = h.control()(session(), 'pause'); h.navigate('B', [row('7')]);
  h.context.workerViewTask.current = 'A'; // navigation render before A's list effect/read
  h.controls[0].resolve({ id: 7, state: 'paused' }); await pending;
  assert.equal(h.state.intents[0].state, 'running'); assert.equal(h.context.firstIntentsRef.current.taskId, 'B');
  assert.equal(h.context.firstIntentsRef.current.items[0].state, 'running'); assert.deepEqual(h.messages, []);
});
test('uncertainty arriving on another task is latched immediately and stays blocked on return', async () => {
  for (const payload of [{ unknown: true }, { pending: true }, { retry: false }]) {
    const h = harness(); const a = h.control()(session(), 'pause'); h.navigate('B');
    h.controls[0].reject(new ApiError(409, payload, 'unconfirmed')); await a;
    assert.deepEqual(h.messages, []); assert.equal(h.context.uncertainControls.current.has('A:7'), true);
    h.navigate('A', [row()]); await h.control()(session(), 'pause'); assert.equal(h.controls.length, 1);
    assert.match(h.messages.at(-1)[1], /Do not repeat/);
  }
});
test('known failure releases pending without claiming a state change', async () => {
  const h = harness(); const first = h.control()(session(), 'pause');
  h.controls[0].reject(new ApiError(409, {}, 'not running')); await first;
  assert.equal(h.context.uncertainControls.current.size, 0); assert.equal(h.state.intents[0].state, 'running');
  const second = h.control()(session(), 'pause'); assert.equal(h.controls.length, 2);
  h.controls[1].resolve({ id: 7, state: 'paused' }); await second;
});
test('unmount suppresses control state, toasts, dialog changes and follow-up reads', async () => {
  for (const fails of [false, true]) {
    const h = harness(); const pending = h.control()(session(), 'pause'); h.unmount(); const before = h.changes.length;
    if (fails) h.controls[0].reject(new ApiError(0, { unknown: true }, 'lost')); else h.controls[0].resolve({ state: 'paused' });
    await pending; assert.equal(h.changes.length, before); assert.deepEqual(h.messages, []); assert.equal(h.reads.length, 0);
    assert.equal(h.context.controlRequests.current.size, 0);
  }
});
test('obsolete handler, unloaded task, inherited worker and Python cancellation cannot dispatch', async () => {
  const h = harness(); const obsolete = h.control(); h.navigate('B'); await obsolete(session(), 'pause');
  h.context.firstIntentsRef.current.taskId = null; await h.control()(session('8'), 'pause');
  h.navigate('A', [row()]); await h.control()({ ...session(), inherited: true }, 'pause');
  h.context.PYTHON_PROFILE = true; h.cancel(); await h.control()(session(), 'cancel', 'reason', 'hard');
  assert.equal(h.controls.length, 0); assert.match(h.messages.at(-1)[1], /unsupported/);
});
test('Go soft/hard cancellation retains source semantics and closes only its own original dialog', async () => {
  for (const mode of ['soft', 'hard']) {
    const h = harness(); const target = h.cancel(); const pending = h.control()(target, 'cancel', 'original reason', mode);
    assert.deepEqual(h.controls[0].args, ['A', '7', 'cancel', 'original reason', mode]);
    h.controls[0].resolve({ id: 7, state: mode === 'soft' ? 'deleted' : '', deleted: { intents: 1, facts: 0, findings: 0, activities: 0 } });
    await pending; assert.equal(h.state.intents.length, mode === 'hard' ? 0 : 1);
    if (mode === 'soft') assert.equal(h.state.intents[0].state, 'deleted');
    assert.equal(h.context.cancelTarget.current, null); assert.equal(h.state.cancelReason, '');
  }
});
test('a newer same-task dialog is not closed or cleared by an older cancellation', async () => {
  const h = harness(); const old = h.cancel(); const pending = h.control()(old, 'cancel', 'reason', 'soft');
  const newer = h.cancel('8'); h.state.cancelReason = 'new reason';
  h.controls[0].resolve({ id: 7, state: 'deleted' }); await pending;
  assert.equal(h.context.cancelTarget.current, newer); assert.equal(h.state.cancelIntent, newer); assert.equal(h.state.cancelReason, 'new reason');
});
test('stale confirmation selector cannot cancel a worker for a different dialog/task', async () => {
  const h = harness(); h.cancel('8'); await h.control()(session(), 'cancel', 'reason', 'hard');
  h.context.cancelTarget.current.controlTaskId = 'B'; await h.control()(session('8'), 'cancel', 'reason', 'hard');
  assert.equal(h.controls.length, 0);
});
test('a stale confirmation object cannot control a reopened dialog for the same worker', async () => {
  const h = harness(); const old = h.cancel(); const newer = h.cancel();
  await h.control()(old, 'cancel', 'old reason', 'hard'); assert.equal(h.controls.length, 0);
  const pending = h.control()(newer, 'cancel', 'new reason', 'soft'); assert.equal(h.controls.length, 1);
  h.controls[0].resolve({ id: 7, state: 'deleted' }); await pending;
});
test('duplicate older-page clicks coalesce before render and known failure allows a later read', async () => {
  const h = harness(); const older = h.older(); older(); older(); assert.equal(h.reads.length, 1);
  h.reads[0].reject(new Error('offline')); await flush(); assert.equal(h.state.loadingOlderIntents, false);
  older(); assert.equal(h.reads.length, 2); h.reads[1].resolve(page([row('6')])); await flush();
  assert.equal(h.state.olderIntents[0].id, '6');
});
test('old A page success/finally cannot append into B or clear B loading state', async () => {
  const h = harness(); h.older()(); h.navigate('B', [row('20')]); h.older()(); const b = h.context.olderWorkerRead.current;
  h.reads[0].resolve(page([row('6')], true)); await flush();
  assert.equal(h.state.olderIntents.length, 0); assert.equal(h.state.loadingOlderIntents, true); assert.equal(h.context.olderWorkerRead.current, b);
  h.reads[1].resolve(page([row('19')])); await flush(); assert.equal(h.state.olderIntents[0].id, '19');
});
test('A to B to A older reads use request identity, not only a repeated task ID', async () => {
  const h = harness(); h.older()(); h.navigate('B', [row('20')]); h.older()(); h.navigate('A'); h.older()();
  h.reads[0].resolve(page([row('1')])); h.reads[1].resolve(page([row('19')])); await flush();
  assert.equal(h.state.olderIntents.length, 0); h.reads[2].resolve(page([row('6')])); await flush();
  assert.equal(h.state.olderIntents[0].id, '6');
});
test('unmounted older-page completion cannot change rows or loading state', async () => {
  const h = harness(); h.older()(); h.unmount(); const before = h.changes.length;
  h.reads[0].resolve(page([row('6')])); await flush(); assert.equal(h.changes.length, before);
});
test('first-page polling coalesces slow reads and its cleanup prevents new or late work', async () => {
  const h = harness(); const cleanup = h.poll(); const load = h.intervals.at(-1); load(); assert.equal(h.reads.length, 1);
  cleanup(); const before = h.changes.length; load(); h.reads[0].resolve(page()); await flush();
  assert.equal(h.reads.length, 1); assert.equal(h.changes.length, before); assert.equal(h.context.reloadWorkers.current, null);
});
test('cleanup/setup replay starts a fresh same-task read even if the retired fetch hangs', async () => {
  const h = harness(); const cleanup = h.poll(); const retired = h.context.firstWorkerRead.current;
  cleanup(); assert.equal(h.context.firstWorkerRead.current, null);
  h.poll(); assert.equal(h.reads.length, 2); assert.notEqual(h.context.firstWorkerRead.current, retired);
  h.reads[1].resolve(page([row('8')])); await flush(); assert.equal(h.state.intents[0].id, '8');
  // The retired promise may never settle; it is not a prerequisite for readiness.
  h.reads[0].resolve(page([row('7')])); await flush(); assert.equal(h.state.intents[0].id, '8');
});
test('return navigation does not coalesce against a hanging first read from the old visit', async () => {
  const h = harness(); const cleanupA = h.poll(); cleanupA(); h.navigate('B');
  const cleanupB = h.poll(); cleanupB(); h.navigate('A', [row()]); h.poll();
  assert.equal(h.reads.length, 3); h.reads[2].resolve(page([row('9')])); await flush();
  assert.equal(h.state.intentsTaskId, 'A'); assert.equal(h.state.intents[0].id, '9');
  h.reads[0].resolve(page([row('7')])); h.reads[1].resolve(page([row('8')])); await flush();
  assert.equal(h.state.intents[0].id, '9');
});
test('late old cleanup cannot retire a replacement effect read or loader', async () => {
  const h = harness(); const oldCleanup = h.poll();
  h.context.workerEpochs.current.set('A', 2); h.poll(); // replacement after a control generation changed
  const replacement = h.context.firstWorkerRead.current, loader = h.context.reloadWorkers.current;
  assert.equal(h.reads.length, 2); oldCleanup();
  assert.equal(h.context.firstWorkerRead.current, replacement); assert.equal(h.context.reloadWorkers.current, loader);
  h.reads[0].resolve(page([row('7')])); await flush(); assert.equal(h.context.firstWorkerRead.current, replacement);
  h.reads[1].resolve(page([row('8')])); await flush(); assert.equal(h.state.intents[0].id, '8');
});
test('navigation fences first-page responses and establishes readiness only for the matching task', async () => {
  const h = harness(); const oldCleanup = h.poll(); oldCleanup(); h.navigate('B'); h.poll();
  h.reads[0].resolve(page([row('7')])); await flush(); assert.equal(h.state.intentsTaskId, null);
  h.reads[1].resolve(page([row('8')])); await flush(); assert.equal(h.state.intentsTaskId, 'B');
  assert.equal(h.context.firstIntentsRef.current.taskId, 'B'); assert.equal(h.state.intents[0].id, '8');
});
test('poll and older pages started before control cannot overwrite the confirmed worker state', async () => {
  const h = harness(); h.poll(); h.reads[0].resolve(page()); await flush();
  h.intervals.at(-1)(); h.older()(); assert.equal(h.reads.length, 3);
  const pending = h.control()(session(), 'pause'); assert.equal(h.state.loadingOlderIntents, false);
  h.intervals.at(-1)(); h.older()(); assert.equal(h.reads.length, 3); // no reads while controlling
  h.controls[0].resolve({ id: 7, state: 'paused' }); await pending;
  assert.equal(h.reads.length, 4); // completion explicitly starts a fresh first page
  h.reads[1].resolve(page([row('7', 'running')])); h.reads[2].resolve(page([row('6', 'running')])); await flush();
  assert.equal(h.state.intents[0].state, 'paused'); assert.equal(h.state.olderIntents.length, 0);
  h.reads[3].resolve(page([row('7', 'paused')])); await flush(); assert.equal(h.state.intents[0].state, 'paused');
});
test('confirmed cache patch survives first-page displacement into older rows', async () => {
  const h = harness(); h.poll(); h.reads[0].resolve(page()); await flush();
  const pending = h.control()(session(), 'pause'); h.controls[0].resolve({ id: 7, state: 'paused' }); await pending;
  h.reads[1].resolve(page([row('10')], true)); await flush();
  assert.equal(h.state.olderIntents[0].id, '7'); assert.equal(h.state.olderIntents[0].state, 'paused');
});
test('poll failure releases its request token and a later interval retries without changing current rows', async () => {
  const h = harness(); h.poll(); h.reads[0].resolve(page()); await flush(); h.intervals.at(-1)();
  h.reads[1].reject(new Error('offline')); await flush(); assert.equal(h.state.intents[0].id, '7');
  h.intervals.at(-1)(); assert.equal(h.reads.length, 3); h.reads[2].resolve(page([row('8')])); await flush();
  assert.equal(h.state.intents[0].id, '8');
});
test('actual UI gates cover readiness, task-wide pending control and task-bound deletion dialog', () => {
  assert.match(source, /disabled=\{loadingOlderIntents \|\| !workersReady \|\| controllingIntent !== null\}/);
  assert.match(source, /controlling=\{!workersReady \|\| controllingIntent !== null/);
  assert.match(source, /<AlertDialog\s+key=\{taskId\}\s+open=\{cancelIntent !== null && cancelIntent.controlTaskId === taskId\}/);
  assert.match(source, /disabled=\{PYTHON_PROFILE \|\| !workersReady \|\| !cancelIntent \|\| cancelIntent.controlTaskId !== taskId/);
  assert.match(source, /\[\.\.\.unconfirmedControls\]\.some\(\(key\) => key.startsWith\(`\$\{taskId\}:`\)\)/);
});
