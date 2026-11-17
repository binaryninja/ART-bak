// The ring's seq and server_logs.id are separate namespaces. History has seq=0;
// a replay may add db_id to a live row after Go's asynchronous database write.
export const LOG_ROW_LIMIT = 5000;

function validLine(line) {
  return line !== null && typeof line === "object" &&
    Number.isSafeInteger(line.seq) && line.seq >= 0 &&
    (line.db_id === undefined || (Number.isSafeInteger(line.db_id) && line.db_id >= 0)) &&
    ["info", "warn", "error"].includes(line.level) &&
    [line.ts, line.tag, line.text].every((value) => typeof value === "string");
}

function liveKey(line) {
  // A process restart can reuse seq. Never discard a different line solely
  // because it has the same counter, and never deduplicate by text alone.
  return JSON.stringify([line.seq, line.ts, line.level, line.tag, line.text]);
}

export function logRowKey(line) {
  return line.db_id ? `db:${line.db_id}` : `live:${liveKey(line)}`;
}

export function oldestLogId(lines) {
  return lines.reduce((oldest, line) => line.db_id && (!oldest || line.db_id < oldest) ? line.db_id : oldest, 0);
}

export function mergeLogRows(previous, incoming, prepend = false, limit = LOG_ROW_LIMIT) {
  const rows = [];
  const database = new Map();
  const live = new Map();
  for (const line of prepend ? [...incoming, ...previous] : [...previous, ...incoming]) {
    const byId = line.db_id ? database.get(line.db_id) : undefined;
    // Different persisted IDs remain distinct even if a restarted process
    // reuses both the sequence and timestamp for an identical log message.
    const bySeq = line.seq > 0 ? (live.get(liveKey(line)) ?? []).find((row) =>
      !row.removed && (!line.db_id || !row.line.db_id || line.db_id === row.line.db_id)) : undefined;
    const row = byId ?? bySeq ?? { line, removed: false };
    if (!byId && !bySeq) rows.push(row);
    if (byId && bySeq && byId !== bySeq) {
      bySeq.removed = true;
      row.line = { ...bySeq.line, db_id: line.db_id };
    }
    const richer = line.seq > 0 ? line : row.line;
    row.line = { ...richer, ...(line.db_id || row.line.db_id ? { db_id: line.db_id || row.line.db_id } : {}) };
    if (row.line.db_id) database.set(row.line.db_id, row);
    if (line.seq > 0) {
      const key = liveKey(line);
      live.set(key, [...(live.get(key) ?? []).filter((entry) => entry !== row && !entry.removed), row]);
    }
  }
  const merged = rows.filter((row) => !row.removed).map((row) => row.line);
  // History and replay can complete in either order. Their common ordering is
  // database ID, not seq or wall time. Unpersisted rows retain arrival order.
  const persisted = merged.filter((line) => line.db_id).sort((a, b) => a.db_id - b.db_id);
  let index = 0;
  return merged.map((line) => line.db_id ? persisted[index++] : line).slice(-limit);
}

export function emptyLogHistory() {
  return { lines: [], hasMore: true, loadingHistory: false, error: null };
}

export function createLogHistory({ read, onChange, limit = LOG_ROW_LIMIT }, clock = globalThis) {
  let state = emptyLogHistory();
  let active = false;
  let current = null;

  function publish(next) {
    state = next;
    onChange(state);
  }

  function retire() {
    const old = current;
    current = null;
    if (old) {
      clock.clearTimeout(old.timer);
      old.controller.abort();
    }
  }

  function activate() {
    retire();
    active = true;
    publish({ ...state, loadingHistory: false });
  }

  function deactivate() {
    active = false;
    retire();
  }

  function clear() {
    retire();
    if (active) publish(emptyLogHistory());
  }

  function append(line) {
    if (!active || !validLine(line) || line.seq <= 0) return false;
    publish({ ...state, lines: mergeLogRows(state.lines, [line], false, limit) });
    return true;
  }

  function loadOlder() {
    if (!active || current || !state.hasMore || state.lines.length >= limit) return false;
    const before = oldestLogId(state.lines);
    const size = Math.min(200, limit - state.lines.length);
    const path = `/logs/history?${before ? `before=${before}&` : ""}limit=${size}`;
    const owner = { controller: new AbortController(), timer: undefined };
    current = owner;
    publish({ ...state, loadingHistory: true, error: null });

    function finish(error, data) {
      if (!active || current !== owner) return;
      current = null;
      clock.clearTimeout(owner.timer);
      let next = state;
      if (!error) {
        if (!data || !Array.isArray(data.items) || typeof data.has_more !== "boolean" ||
            data.items.length > size || (data.has_more && data.items.length === 0) ||
            data.items.some((line) => !validLine(line) || line.seq !== 0 || !line.db_id || (before && line.db_id >= before))) {
          error = new Error("Invalid log history response");
        } else {
          next = { ...state, lines: mergeLogRows(state.lines, data.items, true, limit), hasMore: data.has_more };
        }
      }
      publish({ ...next, loadingHistory: false, error: error ? "Could not load earlier logs. Try again." : null });
    }

    owner.timer = clock.setTimeout(() => {
      finish(new Error("Log history timed out"));
      owner.controller.abort();
    }, 15_000);
    Promise.resolve().then(() => {
      if (active && current === owner) return read(path, owner.controller.signal);
    }).then((data) => finish(null, data), (error) => finish(error || new Error("Log history failed")));
    return true;
  }

  return Object.freeze({ activate, deactivate, clear, append, loadOlder });
}
