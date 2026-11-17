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
// Unlike the general helper, a deletion target can never omit its file path:
// DELETE on the optional root route would be a different whole-skill action.
export function skillDeletionPath(skill, path) {
  if (typeof path !== "string" || !path) throw new Error("Deletion requires a nonempty relative file path");
  return skillResourcePath(skill, path);
}
function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function confirmed(value) {
  return object(value) && value.pending !== true && value.unknown !== true && value.retry !== false;
}
// The explicit Skills host reports configured mode, not authority or a promise
// that the next write will be accepted. Unknown hosts never enable Python writes.
export function confirmedSkillWorkspaceProfile(value) {
  if (
    !object(value) ||
    ![2, 3].includes(Object.keys(value).length) ||
    Object.keys(value).some((key) => !["profile", "writes_enabled", "deletes_enabled"].includes(key)) ||
    value.profile !== "skills-workspace" ||
    typeof value.writes_enabled !== "boolean" ||
    ("deletes_enabled" in value && typeof value.deletes_enabled !== "boolean") ||
    (value.deletes_enabled === true && value.writes_enabled !== true)
  ) {
    throw new Error("Skills host mode response is unavailable or incompatible. Writes remain disabled.");
  }
  return {
    profile: "skills-workspace",
    writes_enabled: value.writes_enabled,
    ...("deletes_enabled" in value ? { deletes_enabled: value.deletes_enabled } : {}),
  };
}
export function validSkillCreationName(name) {
  if (typeof name !== "string") return false;
  const chars = [...name];
  const letter = (c) => /[a-z]/u.test(c) || (c.codePointAt(0) > 127 && /\p{L}/u.test(c));
  return (
    chars.length >= 1 &&
    chars.length <= 64 &&
    letter(chars[0]) &&
    !name.endsWith("-") &&
    !name.includes("--") &&
    chars.every((c) => letter(c) || c === "-" || /\p{Nd}/u.test(c)) &&
    new TextEncoder().encode(name).length <= 255
  );
}
export function confirmedSkillCreation(value, name) {
  if (!validSkillCreationName(name) || !confirmed(value) || value.outcome === "unknown" || value.name !== name)
    throw new ApiError(
      201,
      { unknown: true, retry: false },
      "Skill creation is unconfirmed. Inspect the workspace before any further writes.",
    );
  return { name };
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
export function confirmedSkillDeletion(value, path) {
  let canonical = false;
  try {
    skillDeletionPath("receipt", path);
    canonical = true;
  } catch {
    /* Missing or invalid request identity cannot acknowledge deletion. */
  }
  if (
    !canonical ||
    !confirmed(value) ||
    Object.keys(value).length !== 1 ||
    !Object.hasOwn(value, "deleted") ||
    value.deleted !== path
  )
    throw new ApiError(
      200,
      { unknown: true, retry: false },
      "Deletion is unconfirmed. Do not repeat it; inspect the workspace before further mutations.",
    );
  return { deleted: path };
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

export function createSkillWorkspace(api, { requireProfile = false } = {}) {
  const drafts = new Map(),
    reads = new Map(),
    pending = new Map(),
    uncertain = new Map(),
    errors = new Map(),
    deletedPaths = new Map();
  const listeners = new Set();
  let snapshot;
  let profile = requireProfile
    ? { status: "unchecked", writesEnabled: false, deletesEnabled: false, error: null }
    : null;
  let profileRequest = null;
  const writeModeEnabled = () => profile === null || (profile.status === "ready" && profile.writesEnabled);
  const deleteModeEnabled = () =>
    profile === null || (profile.status === "ready" && profile.writesEnabled && profile.deletesEnabled);
  let deletionUncertain = null;
  const deletionPending = () => [...pending.values()].find((item) => item.kind === "delete") ?? null;
  const within = (path, parent) => path === parent || path.startsWith(`${parent}/`);
  const wasDeleted = (skill, path) =>
    [...deletedPaths.values()].some((item) => item.skill === skill && within(path, item.path));
  let rootPending = null,
    rootUncertain = null,
    rootError = null;
  function publish() {
    snapshot = {
      drafts: new Map(drafts),
      pending: new Map(pending),
      uncertain: new Map(uncertain),
      errors: new Map(errors),
      rootPending,
      rootUncertain,
      rootError,
      profile,
      writeModeEnabled: writeModeEnabled(),
      deleteModeEnabled: deleteModeEnabled(),
      deletionUncertain,
      deletionPending: deletionPending(),
      deletedPaths: new Map(deletedPaths),
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
        deleted: wasDeleted(skill, path),
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
    readProfile() {
      if (!requireProfile)
        return () => {
          /* Go/mock has no host-profile read owner. */
        };
      profileRequest?.abort();
      const owner = new AbortController();
      profileRequest = owner;
      profile = { status: "loading", writesEnabled: false, deletesEnabled: false, error: null };
      publish();
      const current = () => profileRequest === owner;
      Promise.resolve()
        .then(() => (current() ? api.skillWorkspaceProfile(owner.signal) : undefined))
        .then((value) => {
          if (!current()) return;
          const receipt = confirmedSkillWorkspaceProfile(value);
          profile = {
            status: "ready",
            writesEnabled: receipt.writes_enabled,
            deletesEnabled: receipt.deletes_enabled === true,
            error: null,
          };
        })
        .catch((error) => {
          if (current())
            profile = { status: "error", writesEnabled: false, deletesEnabled: false, error: errorText(error) };
        })
        .finally(() => {
          if (!current()) return;
          profileRequest = null;
          publish();
        });
      return () => {
        if (!current()) return;
        profileRequest = null;
        owner.abort();
        profile = { status: "unchecked", writesEnabled: false, deletesEnabled: false, error: null };
        publish();
      };
    },
    // The effect owns this exact token. Cleanup retires hanging reads without
    // clearing a replacement effect's token (including StrictMode replay).
    read(skill, path) {
      const key = skillFileKey(skill, path),
        entry = draft(skill, path);
      if (entry.deleted) {
        // A stale catalog can select a removed path with no prior cached draft.
        // Publish its tombstone instead of leaving the page in a loading state.
        replace(key, { loaded: true, loading: false });
        publish();
        return () => {
          /* Deleted paths never acquire another read owner. */
        };
      }
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
    async createSkill(input) {
      // Top-level creation shares a synchronous fence with file/folder writes.
      // An unknown root mutation suspends writes across every skill, not only
      // the requested name. Reads, draft edits and catalog refresh stay usable.
      if (!writeModeEnabled() || rootPending || rootUncertain || deletionUncertain || pending.size || uncertain.size)
        return null;
      if (!validSkillCreationName(input?.name) || typeof input?.description !== "string" || !input.description.trim()) {
        rootError = {
          name: typeof input?.name === "string" ? input.name : "",
          message: "Enter a valid skill name and nonblank description.",
        };
        publish();
        return null;
      }
      const request = { ...input, ...(input.mcps ? { mcps: [...input.mcps] } : {}) };
      const token = { name: request.name };
      rootPending = token;
      rootError = null;
      publish();
      try {
        return confirmedSkillCreation(await api.createSkill(request), token.name);
      } catch (error) {
        rootError = { name: token.name, message: errorText(error) };
        if (mutationMustWait(error) || (error instanceof ApiError && error.status >= 500)) {
          rootUncertain = { ...token, message: errorText(error) };
        }
        return null;
      } finally {
        if (rootPending === token) rootPending = null;
        publish();
      }
    },
    async remove(skill, path) {
      // Mode is only a hint. The host independently checks its explicit deletion
      // authority. Legacy mode replies never enable this destructive operation.
      if (!deleteModeEnabled() || rootPending || rootUncertain || deletionUncertain || pending.size || uncertain.size)
        return false;
      try {
        skillDeletionPath(skill, path);
        if (requireProfile && /^skill\.md$/iu.test(path))
          throw new Error("The skill's required SKILL.md cannot be deleted here.");
        if (wasDeleted(skill, path))
          throw new Error("This path was already deleted. Reconcile before another mutation.");
        for (const [key, entry] of drafts) {
          const [draftSkill, draftPath] = JSON.parse(key);
          if (draftSkill === skill && within(draftPath, path) && entry.dirty)
            throw new Error(
              "This path contains an unsaved draft. Save it or keep editing; deletion is blocked while the draft is unsaved.",
            );
        }
      } catch (error) {
        errors.set(skill, { path, message: errorText(error) });
        publish();
        return false;
      }
      const token = { path, kind: "delete" };
      pending.set(skill, token);
      errors.delete(skill);
      publish();
      try {
        confirmedSkillDeletion(await api.deleteSkillPath(skill, path), path);
        deletedPaths.set(skillFileKey(skill, path), { skill, path });
        // Preserve all cached text, including edits made after dispatch. Retire
        // late reads and mark removed paths so Save cannot silently recreate them.
        for (const [key] of drafts) {
          const [draftSkill, draftPath] = JSON.parse(key);
          if (draftSkill === skill && within(draftPath, path)) {
            reads.delete(key);
            replace(key, { deleted: true, loaded: true, loading: false, error: null });
          }
        }
        return true;
      } catch (error) {
        errors.set(skill, { path, message: errorText(error) });
        if (mutationMustWait(error) || (error instanceof ApiError && error.status >= 500))
          deletionUncertain = { skill, ...token, message: errorText(error) };
        return false;
      } finally {
        if (pending.get(skill) === token) pending.delete(skill);
        publish();
      }
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
    if (
      !writeModeEnabled() ||
      rootPending ||
      rootUncertain ||
      deletionUncertain ||
      deletionPending() ||
      pending.has(skill) ||
      uncertain.has(skill)
    )
      return false;
    try {
      skillResourcePath(skill, path);
      if (wasDeleted(skill, path))
        throw new Error("This path was deleted. Copy retained text and reconcile before recreating it.");
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
