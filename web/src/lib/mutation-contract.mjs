// Pure wire-contract helpers. No fetch, retries, storage, or backend selection.
export class ApiError extends Error {
  constructor(status, payload, fallback) {
    const p = payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {};
    super(typeof p.error === 'string' && p.error.trim() ? p.error.trim().slice(0, 2048) : fallback);
    this.name = 'ApiError';
    this.status = Number.isInteger(status) ? status : 0;
    this.pending = p.pending === true;
    this.unknown = p.unknown === true;
    this.retry = p.retry === false ? false : undefined;
  }
}
export function mutationMustWait(error) {
  return error instanceof ApiError && (error.pending || error.unknown || error.retry === false);
}
export function isPythonProfile(value) { return value === "python"; }
export function toolsVersionHeaders(digest, required = false) {
  // Older Go servers omit the field. Never synthesize a digest from tool names.
  if (digest === undefined && !required) return {};
  if (typeof digest !== 'string' || !/^[0-9a-f]{64}$/.test(digest)) throw new Error('Invalid configuration version. Reload before editing.');
  return { 'X-Intercept-Tools-Digest': digest };
}
export function ruleVersionHeaders(version) {
  if (typeof version !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(version)) throw new Error('Rule version is missing or invalid. Reload the rules before editing.');
  return { 'X-Rule-Updated-At': version };
}
export function confirmedIntentState(result, action) {
  const expected = action === 'pause' ? 'paused' : action === 'resume' ? 'open' : null;
  if (expected && (!result || result.state !== expected || result.pending === true || result.unknown === true || result.retry === false)) {
    throw new ApiError(200, { unknown: true, retry: false }, 'Worker operation is unconfirmed. Do not repeat it; reconciliation is required.');
  }
  return expected;
}

// Single-task control is synchronous HTTP 200 in both Go and Python. A queued
// resume is confirmed admission, not a claim that exploration is already running.
export function confirmedTaskControl(result, id, action) {
  const valid = result && typeof result === 'object' && !Array.isArray(result)
    && result.id === id && typeof result.paused === 'boolean' && typeof result.queued === 'boolean'
    && result.pending !== true && result.unknown !== true && result.retry !== false
    && (action === 'pause'
      ? result.paused && !result.queued && result.status === 'paused'
      : action === 'resume' && !result.paused
        && (result.queued ? result.status === 'queued' : result.status === 'running'));
  if (!valid) throw new ApiError(200, { unknown: true, retry: false }, 'Task control is unconfirmed. Do not repeat it; reconciliation is required.');
  return { id: result.id, paused: result.paused, queued: result.queued, status: result.status };
}
