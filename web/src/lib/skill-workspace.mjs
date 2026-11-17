// Skill file contract and mounted-workspace state. No fetch retries, storage,
// filesystem authority, server CAS or operation reconciliation is introduced.
import { ApiError, mutationMustWait } from "./mutation-contract.mjs";

export function skillFileKey(skill, path) {
  return JSON.stringify([skill, path]);
}
function hasControl(value) {
  return [...value].some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127);
}
export function skillResourcePath(skill, path) {
  // Encode each segment once; never let a caller supply URL syntax or traversal.
  if (
    typeof skill !== "string" ||
    !skill ||
    /[\\/]/u.test(skill) ||
    hasControl(skill) ||
    skill === "." ||
    skill === ".."
  )
    throw new Error("Invalid skill name");
  const root = `/skills/${encodeURIComponent(skill)}`;
  if (path === undefined) return root;
  if (
    typeof path !== "string" ||
    !path ||
    path.includes("..") ||
    /[\\%#?*:"<>|\ufffd\p{Cf}\p{Co}\p{Cs}]/u.test(path) ||
    hasControl(path) ||
    [...path].some((c) => c !== " " && /\s/u.test(c)) ||
    path.split("/").some((p) => !p || p === ".")
  )
    throw new Error("Use a canonical relative file path");
  return `${root}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
}
function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function confirmed(value) {
  return object(value) && value.pending !== true && value.unknown !== true && value.retry !== false;
}
export function confirmedSkillWrite(value) {
  if (!confirmed(value) || value.ok !== true)
    throw new ApiError(
      200,
      { unknown: true, retry: false },
      "File write is unconfirmed. Inspect the file before any further writes.",
    );
  return { ok: true };
}
export function confirmedSkillDirectory(value, path) {
  if (!confirmed(value) || value.dir !== path)
    throw new ApiError(
      201,
      { unknown: true, retry: false },
      "Folder operation is unconfirmed. Inspect the skill before any further writes.",
    );
  return { dir: path };
}
export function confirmedSkillContent(value, path) {
  if (!object(value) || value.file !== path || typeof value.content !== "string")
    throw new Error("File response did not match the requested path");
  return value.content;
}
// Catalog errors must not masquerade as an empty successful library.
export function confirmedSkillCatalog(value) {
  if (
    !object(value) ||
    !Array.isArray(value.skills) ||
    value.skills.some(
      (item) =>
        !object(item) ||
        typeof item.name !== "string" ||
        !item.name ||
        !Array.isArray(item.files) ||
        item.files.some((file) => typeof file !== "string") ||
        typeof item.calls !== "number" ||
        !Number.isFinite(item.calls) ||
        typeof item.tasks !== "number" ||
        !Number.isFinite(item.tasks) ||
        !Array.isArray(item.usage_agents) ||
        item.usage_agents.some((agent) => typeof agent !== "string"),
    )
  )
    throw new Error("Invalid skill catalog response");
  return value.skills;
}
const errorText = (error) => (error instanceof Error ? error.message : "Request failed");

export function createSkillWorkspace(api) {
  const drafts = new Map(),
    reads = new Map(),
    pending = new Map(),
    uncertain = new Map(),
    errors = new Map();
  const listeners = new Set();
  let snapshot;
  function publish() {
    snapshot = {
      drafts: new Map(drafts),
      pending: new Map(pending),
      uncertain: new Map(uncertain),
      errors: new Map(errors),
    };
    for (const listener of listeners) listener();
  }
  function draft(skill, path) {
    const key = skillFileKey(skill, path);
    if (!drafts.has(key))
      drafts.set(key, {
        content: "",
        baseline: "",
        revision: 0,
        loaded: false,
        loading: false,
        dirty: false,
        error: null,
      });
    return drafts.get(key);
  }
  function replace(key, fields) {
    drafts.set(key, { ...drafts.get(key), ...fields });
  }
  publish();
  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSnapshot() {
      return snapshot;
    },
    // The effect owns this exact token. Cleanup retires hanging reads without
    // clearing a replacement effect's token (including StrictMode replay).
    read(skill, path) {
      const key = skillFileKey(skill, path),
        entry = draft(skill, path);
      if (entry.loaded)
        return () => {
          /* No read owner exists for a cached draft. */
        };
      const token = { revision: entry.revision };
      reads.set(key, token);
      replace(key, { loading: true, error: null });
      publish();
      Promise.resolve()
        .then(() => api.readSkillFile(skill, path))
        .then((content) => {
          if (reads.get(key) !== token || drafts.get(key).revision !== token.revision) return;
          if (typeof content !== "string") throw new Error("Invalid file content response");
          replace(key, { content, baseline: content, loaded: true, dirty: false });
        })
        .catch((error) => {
          if (reads.get(key) === token) replace(key, { error: errorText(error) });
        })
        .finally(() => {
          if (reads.get(key) === token) {
            reads.delete(key);
            replace(key, { loading: false });
            publish();
          }
        });
      return () => {
        if (reads.get(key) === token) {
          reads.delete(key);
          replace(key, { loading: false });
          publish();
        }
      };
    },
    edit(skill, path, content) {
      const key = skillFileKey(skill, path),
        entry = drafts.get(key);
      if (!entry?.loaded || typeof content !== "string") return;
      replace(key, { content, revision: entry.revision + 1, dirty: content !== entry.baseline });
      publish();
    },
    async save(skill, path) {
      const entry = drafts.get(skillFileKey(skill, path));
      if (!entry?.loaded || !entry.dirty) return false;
      return mutate(skill, path, "save", entry.content);
    },
    async create(skill, path, kind) {
      if (kind !== "file" && kind !== "dir") return false;
      // Never let an empty-file upsert destroy a locally retained unsaved draft.
      if (drafts.get(skillFileKey(skill, path))?.dirty) {
        errors.set(skill, { path, message: "This file has an unsaved draft. Save or keep editing it instead." });
        publish();
        return false;
      }
      return mutate(skill, path, kind, "");
    },
  };
  async function mutate(skill, path, kind, content) {
    if (pending.has(skill) || uncertain.has(skill)) return false;
    try {
      skillResourcePath(skill, path);
    } catch (error) {
      errors.set(skill, { path, message: errorText(error) });
      publish();
      return false;
    }
    const token = { path, kind },
      key = skillFileKey(skill, path);
    const initialRevision = drafts.get(key)?.revision ?? 0;
    pending.set(skill, token);
    errors.delete(skill);
    reads.delete(key); // A pre-write read cannot replace the acknowledged write.
    if (drafts.has(key)) replace(key, { loading: false });
    publish();
    try {
      if (kind === "dir") confirmedSkillDirectory(await api.createSkillDir(skill, path), path);
      else confirmedSkillWrite(await api.writeSkillFile(skill, path, content));
      if (kind !== "dir") {
        const current = draft(skill, path);
        reads.delete(key);
        const keepDraft = current.loaded && (kind === "save" || current.revision !== initialRevision);
        // An edit made during the write remains in the draft; only the saved
        // baseline advances. Value equality, not edit history, determines dirty.
        replace(key, {
          baseline: content,
          content: keepDraft ? current.content : content,
          loaded: true,
          loading: false,
          dirty: keepDraft && current.content !== content,
          error: null,
        });
      }
      return true;
    } catch (error) {
      errors.set(skill, { path, message: errorText(error) });
      if (mutationMustWait(error)) uncertain.set(skill, token);
      return false;
    } finally {
      if (pending.get(skill) === token) pending.delete(skill);
      // A mutation retired a pre-write initial read. A failed write or even a
      // successful mkdir can leave no reader: expose Retry read, not a spinner.
      // Preserve a replacement owner or a more specific error. Reads never
      // clear mutation uncertainty.
      const entry = drafts.get(key);
      if (entry && !entry.loaded && !entry.error && !reads.has(key))
        replace(key, {
          loading: false,
          error: "File read was interrupted by a write. Retry read to inspect the file.",
        });
      publish();
    }
  }
}
