// Offline React contract tests. Supply installed react/react-test-renderer and
// TypeScript package roots explicitly; no browser, listeners, or dependency install.
// ARTBAK_REACT_TEST_ROOT=/path/package.json ARTBAK_TYPESCRIPT_TEST_ROOT=/path/package.json
// node --test web/integration/answer-observation-react.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const web = process.env.ARTBAK_OBSERVATION_WEB ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const deps = createRequire(process.env.ARTBAK_REACT_TEST_ROOT ?? path.join(web, 'package.json'));
const types = createRequire(process.env.ARTBAK_TYPESCRIPT_TEST_ROOT ?? path.join(web, 'package.json'));
const React = deps('react'), Renderer = deps('react-test-renderer'), ts = types('typescript');
const { act } = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const text = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(text).join('') : node?.props ? text(node.props.children) : '';
const renderedText = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(renderedText).join('') : renderedText(node?.children ?? []);
const answer = (seq = 1, summary = 'Brief summary') => ({ seq, worker: 'planner', kind: 'result', summary, is_error: false, ts: '2026-11-17T00:00:00Z' });

function compile(api) {
  const filename = path.join(web, 'src/components/transcript.tsx');
  const js = ts.transpileModule(readFileSync(filename, 'utf8'), { fileName: filename,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {}, timers = new Map(); let timerId = 0;
  const hosts = new Proxy({}, { get: (_, key) => key });
  vm.runInNewContext(js, { exports, console, process: { env: {} }, setInterval: callback => { timers.set(++timerId, callback); return timerId; }, clearInterval: id => timers.delete(id),
    require(name) {
      if (name === 'react' || name === 'react/jsx-runtime') return deps(name);
      if (name === '@/lib/api') return { api };
      if (name === 'sonner') return { toast: { success() {}, error() {}, info() {} } };
      if (name === '@/components/markdown') return { Markdown: ({ text }) => React.createElement('markdown', null, text) };
      if (name.startsWith('@/components/') || name === 'lucide-react') return hosts;
      throw new Error(`Unexpected import ${name}`);
    },
  }, { filename });
  return { exports, timers };
}

async function mount({ id = 'A', step = answer(), strict = true, fetchDetail } = {}) {
  const calls = [];
  const api = new Proxy({}, { get: (_, method) => (...args) => {
    let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; });
    calls.push({ method, args, resolve, reject }); return promise;
  } });
  const module = compile(api);
  let props = { activity: [step], taskId: id, fetchDetail };
  const render = () => {
    const element = React.createElement(module.exports.Transcript, props);
    return strict ? React.createElement(React.StrictMode, null, element) : element;
  };
  let renderer;
  await act(async () => { renderer = Renderer.create(render(), { createNodeMock: () => ({ closest: () => null }) }); });
  return {
    calls, renderer, api, get text() { return renderedText(renderer.toJSON()); },
    reads: () => calls.filter(c => c.method === 'activityDetail'),
    latest: () => calls.filter(c => c.method === 'activityDetail').at(-1),
    button(label) { const button = renderer.root.findAllByType('Button').find(b => text(b) === label); assert(Boolean(button), `Missing ${label}`); return button; },
    async event(fn) { await act(async () => { fn(); }); },
    async finish(call, value, failed = false) { await act(async () => { failed ? call.reject(value) : call.resolve(value); }); },
    async navigate(id, nextStep = step, nextFetcher = fetchDetail) { props = { activity: [nextStep], taskId: id, fetchDetail: nextFetcher }; await act(async () => renderer.update(render())); },
    async unmount() { await act(async () => renderer.unmount()); },
  };
}

test('full answer failure retains a labelled incomplete summary and bounded explicit retry', async () => {
  const h = await mount();
  try {
    assert.match(h.text, /summary may be incomplete/); assert.deepEqual(h.latest().args, [1, 'A']);
    await h.finish(h.latest(), new Error('unavailable'), true);
    assert.match(h.text, /Full answer unavailable/); assert.match(h.text, /Brief summary/);
    const retry = h.button('Retry full answer').props.onClick, before = h.reads().length;
    await h.event(() => { retry(); retry(); }); assert.equal(h.reads().length, before + 1);
    assert.match(h.text, /Loading full answer/);
    await h.finish(h.latest(), { detail: 'Complete answer\nSecond line' });
    assert.match(h.text, /Complete answer\nSecond line/); assert.doesNotMatch(h.text, /incomplete|unavailable|Brief summary/);
  } finally { await h.unmount(); }
});

test('answer errors do not automatically retry and a second explicit failure stays labelled', async () => {
  const h = await mount();
  try {
    await h.finish(h.latest(), new Error('first'), true); const before = h.reads().length;
    await h.event(() => {}); assert.equal(h.reads().length, before);
    await h.event(() => h.button('Retry full answer').props.onClick()); await h.finish(h.latest(), new Error('second'), true);
    assert.match(h.text, /Full answer unavailable/); assert.equal(h.reads().length, before + 1);
  } finally { await h.unmount(); }
});

test('answer task scope changes with the same sequence cannot show or restore prior text', async () => {
  const h = await mount();
  try {
    const old = h.latest(); await h.navigate('B', answer(1, 'B summary')); const fresh = h.latest();
    assert.deepEqual(fresh.args, [1, 'B']);
    await h.finish(fresh, { detail: 'B full' }); await h.finish(old, { detail: 'A stale' });
    assert.match(h.text, /B full/); assert.doesNotMatch(h.text, /A stale|unavailable/);
    await h.navigate('C', answer(1, 'C summary')); assert.doesNotMatch(h.text, /B full/);
  } finally { await h.unmount(); }
});

test('stale answer rejection cannot overwrite a new task success', async () => {
  const h = await mount();
  try { const old = h.latest(); await h.navigate('B'); await h.finish(h.latest(), { detail: 'B full' }); await h.finish(old, new Error('old'), true); assert.match(h.text, /B full/); assert.doesNotMatch(h.text, /unavailable/); }
  finally { await h.unmount(); }
});

test('confirmed empty detail does not fabricate summary as full output', async () => {
  const h = await mount();
  try { await h.finish(h.latest(), { detail: '' }); assert.doesNotMatch(h.text, /Brief summary|incomplete/); }
  finally { await h.unmount(); }
});

test('error answers retain plain error output after full detail succeeds', async () => {
  const h = await mount({ step: { ...answer(), is_error: true } });
  try { await h.finish(h.latest(), { detail: 'Observed failure' }); assert.match(h.text, /Observed failure/); assert.equal(h.renderer.root.findAllByType('markdown').length, 0); }
  finally { await h.unmount(); }
});

test('answer pending completion and retry are inert after unmount', async () => {
  const h = await mount(); await h.finish(h.latest(), new Error('offline'), true);
  const retry = h.button('Retry full answer').props.onClick; await h.event(retry); const pending = h.latest();
  await h.unmount(); const before = h.reads().length;
  await h.event(retry); await h.finish(pending, { detail: 'late' }); assert.equal(h.reads().length, before);
});

test('answer A/B/A replacement fences both old scope replies even at the same sequence', async () => {
  const h = await mount({ strict: false });
  try {
    const first = h.latest(); await h.navigate('B'); const second = h.latest();
    await h.navigate('A'); const latest = h.latest();
    await h.finish(latest, { detail: 'A current' });
    await h.finish(first, { detail: 'A old' }); await h.finish(second, new Error('B old'), true);
    assert.match(h.text, /A current/); assert.doesNotMatch(h.text, /A old|unavailable/);
  } finally { await h.unmount(); }
});

test('conversation fetcher identity changes reset full text and reject old success', async () => {
  let resolveFirst, resolveSecond;
  const first = () => new Promise(resolve => { resolveFirst = resolve; });
  const second = () => new Promise(resolve => { resolveSecond = resolve; });
  const h = await mount({ strict: false, fetchDetail: first });
  try {
    await h.navigate('A', answer(), second);
    await h.event(() => resolveSecond('New conversation full'));
    await h.event(() => resolveFirst('Old conversation full'));
    assert.match(h.text, /New conversation full/); assert.doesNotMatch(h.text, /Old conversation full/);
    assert.equal(h.reads().length, 0);
  } finally { await h.unmount(); }
});

test('synchronous answer reader errors are displayed and can be retried', async () => {
  let count = 0;
  const fetchDetail = () => { count++; if (count === 1) throw new Error('synchronous failure'); return Promise.resolve('Recovered full'); };
  const h = await mount({ strict: false, fetchDetail });
  try {
    assert.match(h.text, /Full answer unavailable/);
    await h.event(() => h.button('Retry full answer').props.onClick());
    assert.equal(count, 2); assert.match(h.text, /Recovered full/);
  } finally { await h.unmount(); }
});
