"use client";

import * as React from "react";
import { toast } from "sonner";
import {
  ChevronRightIcon,
  FolderIcon,
  FolderOpenIcon,
  FolderPlusIcon,
  FileTextIcon,
  FilePlusIcon,
  PlusIcon,
  Trash2Icon,
  UploadIcon,
  AlertTriangleIcon,
} from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { MOCK } from "@/lib/mock/enabled";
import { isPythonProfile } from "@/lib/mutation-contract.mjs";
import { createSkillWorkspace, skillFileKey, validSkillCreationName } from "@/lib/skill-workspace.mjs";

import type { Agent, SkillItem, MCPServer, SkillCall, MissingSkill } from "@/lib/types";

const RESTRICTED_SKILLS = isPythonProfile(process.env.NEXT_PUBLIC_ARTEX_BACKEND_PROFILE);

type NewSkillDraft = {
  name: string;
  description: string;
  license: string;
  compatibility: string;
  instructions: string;
  mcps: string[];
  visibility: string[];
};
const emptyNewSkillDraft = (): NewSkillDraft => ({
  name: "", description: "", license: "", compatibility: "", instructions: "", mcps: [], visibility: [],
});

function fmtTime(ts?: string) {
  if (!ts) return "Never used";
  return new Date(ts).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ── Tree node ──────────────────────────────────────────────────────────────
interface TreeNode {
  name: string;
  path: string;   // relative to skill root, dirs WITHOUT trailing slash
  type: "file" | "dir";
  children: TreeNode[];
}

// Backend returns dirs as "scripts/" (trailing slash) and files as "scripts/a.py".
function buildTree(entries: string[]): TreeNode[] {
  const root: TreeNode[] = [];
  const dirMap = new Map<string, TreeNode>();

  function ensureDir(dirPath: string, parentNodes: TreeNode[]): TreeNode[] {
    if (dirMap.has(dirPath)) return dirMap.get(dirPath)!.children;
    const parts = dirPath.split("/");
    let nodes = root;
    let cur = "";
    for (const part of parts) {
      cur = cur ? `${cur}/${part}` : part;
      if (!dirMap.has(cur)) {
        const d: TreeNode = { name: part, path: cur, type: "dir", children: [] };
        nodes.push(d);
        dirMap.set(cur, d);
      }
      nodes = dirMap.get(cur)!.children;
    }
    return nodes;
  }

  for (const entry of [...entries].sort()) {
    if (entry.endsWith("/")) {
      // Explicit directory entry — ensure the node exists (may already be created)
      ensureDir(entry.slice(0, -1), root);
    } else {
      // File — ensure parent dirs exist, then push file node
      const parts = entry.split("/");
      let nodes = root;
      let cur = "";
      for (let i = 0; i < parts.length - 1; i++) {
        cur = cur ? `${cur}/${parts[i]}` : parts[i];
        if (!dirMap.has(cur)) {
          const d: TreeNode = { name: parts[i], path: cur, type: "dir", children: [] };
          nodes.push(d);
          dirMap.set(cur, d);
        }
        nodes = dirMap.get(cur)!.children;
      }
      const fname = parts[parts.length - 1];
      if (fname) nodes.push({ name: fname, path: entry, type: "file", children: [] });
    }
  }
  sortNodes(root);
  return root;
}

// sortNodes orders every level like a file explorer: directories before files,
// then case-insensitive by name. Recurses into children.
function sortNodes(nodes: TreeNode[]): void {
  nodes.sort((a, b) => {
    if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  });
  for (const n of nodes) {
    if (n.children.length > 0) sortNodes(n.children);
  }
}

// ── State types ───────────────────────────────────────────────────────────
type Selected =
  | { skill: string; path: null }
  | { skill: string; path: string };

type Creating = {
  skill: string;
  inDir: string;
  kind: "file" | "dir";
} | null;

type PendingDelete =
  | { kind: "skill"; skill: string }
  | { kind: "file"; skill: string; path: string }
  | { kind: "dir"; skill: string; path: string }
  | null;

// ── Overview (empty-state) ──────────────────────────────────────────────────
// Shown when nothing is selected: a library-wide snapshot from data already loaded
// (the skill list carries per-skill usage; missing is fetched alongside). No extra
// requests — this is pure aggregation over props.
function SkillsOverview({
  skills,
  missing,
  onSelect,
}: {
  skills: SkillItem[];
  missing: MissingSkill[];
  onSelect: (name: string) => void;
}) {
  const agg = React.useMemo(() => {
    const totalCalls = skills.reduce((n, s) => n + s.calls, 0);
    const used = skills.filter((s) => s.calls > 0);
    const ranked = [...used].sort((a, b) => b.calls - a.calls);
    const neverUsed = skills.filter((s) => s.calls === 0);
    const recent = skills
      .filter((s) => s.last_used)
      .sort((a, b) => (a.last_used! < b.last_used! ? 1 : -1))
      .slice(0, 6);
    const missingCalls = missing.reduce((n, m) => n + m.calls, 0);
    return {
      totalCalls,
      usedCount: used.length,
      ranked,
      topCalls: ranked[0]?.calls ?? 0,
      neverUsed,
      recent,
      missingCalls,
    };
  }, [skills, missing]);

  if (skills.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-muted-foreground">No skills yet. Click New or Upload archive on the left to begin.</p>
      </div>
    );
  }

  const stats: { label: string; value: React.ReactNode; hint?: string }[] = [
    { label: "Total skills", value: skills.length, hint: `${agg.usedCount} used` },
    { label: "Total calls", value: agg.totalCalls },
    { label: "Unused", value: agg.neverUsed.length, hint: agg.neverUsed.length > 0 ? "Never loaded by any agent" : "All used" },
    { label: "Missing skill calls", value: agg.missingCalls, hint: missing.length > 0 ? `${missing.length} missing skills` : "None" },
  ];

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <div>
        <h2 className="text-base font-semibold">Skill library overview</h2>
        <p className="text-muted-foreground text-sm">Select a skill on the left for details and usage history, or review overall usage here.</p>
      </div>

      {/* Metric cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {stats.map((s) => (
          <div key={s.label} className="rounded-lg border p-3">
            <p className="text-2xl font-semibold tabular-nums">{s.value}</p>
            <p className="text-xs font-medium">{s.label}</p>
            {s.hint && <p className="text-muted-foreground mt-0.5 text-[11px]">{s.hint}</p>}
          </div>
        ))}
      </div>

      {/* Usage ranking */}
      <div className="space-y-2">
        <Label className="text-xs text-muted-foreground">Usage ranking</Label>
        {agg.ranked.length === 0 ? (
          <p className="text-muted-foreground rounded-lg border border-dashed p-4 text-center text-xs">
            No skill calls recorded yet.
          </p>
        ) : (
          <div className="space-y-1.5">
            {agg.ranked.slice(0, 8).map((s) => (
              <button
                key={s.name}
                type="button"
                onClick={() => onSelect(s.name)}
                className="group flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left hover:bg-muted"
              >
                <span className="w-40 shrink-0 truncate font-mono text-xs" title={s.name}>{s.name}</span>
                <span className="relative h-2 flex-1 overflow-hidden rounded-full bg-muted">
                  <span
                    className="absolute inset-y-0 left-0 rounded-full bg-primary/70"
                    style={{ width: `${agg.topCalls > 0 ? (s.calls / agg.topCalls) * 100 : 0}%` }}
                  />
                </span>
                <span className="w-16 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {s.calls} calls
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
        {/* Recent calls */}
        <div className="space-y-2">
          <Label className="text-xs text-muted-foreground">Recent calls</Label>
          {agg.recent.length === 0 ? (
            <p className="text-muted-foreground text-xs">No records yet.</p>
          ) : (
            <div className="space-y-1">
              {agg.recent.map((s) => (
                <button
                  key={s.name}
                  type="button"
                  onClick={() => onSelect(s.name)}
                  className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-muted"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-xs" title={s.name}>{s.name}</span>
                  <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">{fmtTime(s.last_used)}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Unused (candidates for cleanup or discovery) */}
        <div className="space-y-2">
          <Label className="text-xs text-muted-foreground">
            Unused skills
            {agg.neverUsed.length > 0 && <span className="ml-1 font-normal">（{agg.neverUsed.length}）</span>}
          </Label>
          {agg.neverUsed.length === 0 ? (
            <p className="text-muted-foreground text-xs">Every skill has been used.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {agg.neverUsed.map((s) => (
                <button
                  key={s.name}
                  type="button"
                  onClick={() => onSelect(s.name)}
                  title={s.name}
                >
                  <Badge variant="outline" className="max-w-[12rem] cursor-pointer truncate font-mono text-xs font-normal hover:bg-muted">
                    {s.name}
                  </Badge>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────
export default function SkillsPage() {
  const [skills, setSkills] = React.useState<SkillItem[]>([]);
  const [agents, setAgents] = React.useState<Agent[]>([]);
  const [selected, setSelectedState] = React.useState<Selected | null>(null);
  const selectionRef = React.useRef<Selected | null>(null);
  function setSelected(next: Selected | null) {
    selectionRef.current = next;
    setSelectedState(next);
  }
  const catalogRequest = React.useRef<object | null>(null);
  const alive = React.useRef(false);
  React.useEffect(() => {
    alive.current = true;
    setCreatingSkill(newRequest.current !== null);
    return () => { alive.current = false; catalogRequest.current = null; };
  }, []);
  const [visibility, setVisibility] = React.useState<Record<string, string[]>>({});
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());

  // Mounted workspace caches drafts by exact skill/path. The model fences
  // callbacks synchronously, independently of React's next render.
  const [workspace] = React.useState(() => createSkillWorkspace(api, { requireProfile: RESTRICTED_SKILLS && !MOCK }));
  const workspaceState = React.useSyncExternalStore(workspace.subscribe, workspace.getSnapshot, workspace.getSnapshot);
  const fileDraft = selected?.path != null
    ? workspaceState.drafts.get(skillFileKey(selected.skill, selected.path)) : undefined;
  const fileLoading = !fileDraft?.loaded && !fileDraft?.error && !fileDraft?.deleted;
  const saving = !!selected && workspaceState.pending.has(selected.skill);
  const rootWriteBlocked = !workspaceState.writeModeEnabled || !!workspaceState.rootPending || !!workspaceState.rootUncertain || !!workspaceState.deletionPending || !!workspaceState.deletionUncertain || !!workspaceState.importPending || !!workspaceState.importUncertain;
  const blocked = rootWriteBlocked || (!!selected && workspaceState.uncertain.has(selected.skill));
  function rootWritesSuspended() {
    const state = workspace.getSnapshot();
    return !state.writeModeEnabled || !!state.rootPending || !!state.rootUncertain || !!state.deletionPending || !!state.deletionUncertain || !!state.importPending || !!state.importUncertain;
  }
  const [fileRetry, setFileRetry] = React.useState(0);
  const profileCleanup = React.useRef<(() => void) | null>(null);
  const refreshProfile = React.useCallback(() => {
    profileCleanup.current?.();
    profileCleanup.current = workspace.readProfile();
  }, [workspace]);
  React.useEffect(() => {
    refreshProfile();
    return () => { profileCleanup.current?.(); profileCleanup.current = null; };
  }, [refreshProfile]);

  // inline create — using a ref to avoid stale closure in onBlur
  const [creating, setCreating] = React.useState<Creating>(null);
  const [newEntryName, setNewEntryName] = React.useState("");
  const inlineRef = React.useRef<HTMLInputElement>(null);
  // cancelRef prevents onBlur from committing when Escape was pressed
  const cancelRef = React.useRef(false);

  // Dialog state is separate from the latest draft/session identities. Every edit
  // changes the draft object immediately, before React schedules another render.
  const [newOpen, setNewOpenState] = React.useState(false);
  const newDialog = React.useRef({ open: false });
  const [newDraft, setNewDraft] = React.useState(emptyNewSkillDraft);
  const newDraftRef = React.useRef(newDraft);
  const newRequest = React.useRef<object | null>(null);
  const { name: newName, description: newDesc, license: newLicense, compatibility: newCompat,
    instructions: newInst, mcps: newMcps, visibility: newVisibility } = newDraft;
  function editNewSkill(patch: Partial<NewSkillDraft>) {
    if (!alive.current) return;
    const next = { ...newDraftRef.current, ...patch };
    newDraftRef.current = next;
    setNewDraft(next);
  }
  function setNewOpen(value: boolean) {
    if (!alive.current) return;
    if (newDialog.current.open !== value) newDialog.current = { open: value };
    setNewOpenState(value);
  }
  const [mcpOptions, setMcpOptions] = React.useState<MCPServer[]>([]);
  const [creatingSkill, setCreatingSkill] = React.useState(false);
  const [uploading, setUploading] = React.useState(false);
  const uploadRef = React.useRef<HTMLInputElement>(null);

  // Python archive imports are create-only, separate from Go's upload/overwrite.
  const [importOpen, setImportOpenState] = React.useState(false);
  const importDialog = React.useRef({ open: false });
  const importSession = importDialog.current;
  const [importFile, setImportFile] = React.useState<File | null>(null);
  const importFileRef = React.useRef<File | null>(null);
  const importRequest = React.useRef<object | null>(null);
  const importAlive = React.useRef(false);
  const [importing, setImporting] = React.useState(false);
  React.useLayoutEffect(() => {
    importAlive.current = true;
    setImporting(importRequest.current !== null);
    return () => { importAlive.current = false; };
  }, []);
  function setImportOpen(value: boolean) {
    if (!importAlive.current) return;
    if (importDialog.current.open !== value) importDialog.current = { open: value };
    setImportOpenState(value);
  }
  function pickImportFile(e: React.ChangeEvent<HTMLInputElement>, owner: { open: boolean }) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!importAlive.current || !owner.open || importDialog.current !== owner || !file) return;
    importFileRef.current = file;
    setImportFile(file);
  }
  async function importArchive() {
    const owner = importDialog.current;
    const file = importFileRef.current;
    const state = workspace.getSnapshot();
    if (!RESTRICTED_SKILLS || !importAlive.current || !owner.open || owner !== importSession ||
      importRequest.current || !file || !state.importModeEnabled || rootWritesSuspended() ||
      state.pending.size || state.uncertain.size) return;
    const request = {};
    importRequest.current = request;
    setImporting(true);
    try {
      const receipt = await workspace.importArchive(file);
      if (!receipt || !importAlive.current || importRequest.current !== request) return;
      toast.success(`${MOCK ? "Simulated import" : "Imported"}: ${receipt.name} (${receipt.files} files)`);
      load();
      if (importFileRef.current === file && importDialog.current === owner) {
        importFileRef.current = null;
        setImportFile(null);
        setImportOpen(false);
      }
    } finally {
      if (importRequest.current === request) {
        importRequest.current = null;
        if (importAlive.current) setImporting(false);
      }
    }
  }

  // skill detail — MCP edit state (optimistic, rolls back on error)
  const [detailMcps, setDetailMcps] = React.useState<string[]>([]);

  // delete confirmation
  const [pendingDelete, setPendingDeleteState] = React.useState<PendingDelete>(null);
  const pendingDeleteRef = React.useRef<PendingDelete>(null);
  const deleteRequest = React.useRef<object | null>(null);
  const deleteAlive = React.useRef(false);
  const [deleting, setDeleting] = React.useState(false);
  React.useLayoutEffect(() => {
    deleteAlive.current = true;
    setDeleting(deleteRequest.current !== null);
    return () => { deleteAlive.current = false; };
  }, []);
  function setPendingDelete(next: PendingDelete) {
    if (!deleteAlive.current) return;
    pendingDeleteRef.current = next;
    setPendingDeleteState(next);
  }
  function pathDeletionBlocked(skill: string, path: string) {
    const state = workspace.getSnapshot();
    return !state.deleteModeEnabled || !!state.rootPending || !!state.rootUncertain ||
      !!state.deletionUncertain || !!state.importPending || !!state.importUncertain || state.pending.size > 0 || state.uncertain.size > 0 ||
      (RESTRICTED_SKILLS && /^skill\.md$/iu.test(path)) ||
      [...state.deletedPaths.values()].some((item) => item.skill === skill && (path === item.path || path.startsWith(`${item.path}/`)));
  }

  // Usage counts and last-used times arrive with api.skills(); selecting a skill fetches its
  // recent call details. missing contains requested skills absent from the library.
  const [usageCalls, setUsageCalls] = React.useState<SkillCall[]>([]);
  const [usageLoading, setUsageLoading] = React.useState(false);
  const [missing, setMissing] = React.useState<MissingSkill[]>([]);

  // ── Data ──────────────────────────────────────────────────────────────────
  const [catalogLoading, setCatalogLoading] = React.useState(true);
  const [catalogError, setCatalogError] = React.useState<string | null>(null);
  const load = React.useCallback(() => {
    const request = {};
    catalogRequest.current = request;
    setCatalogLoading(true);
    setCatalogError(null);
    const current = () => alive.current && catalogRequest.current === request;
    if (!RESTRICTED_SKILLS) {
      api.agents().then((items) => { if (current()) setAgents(items); }).catch(() => { /* Optional Go-only decoration. */ });
      api.mcpServers().then((items) => { if (current()) setMcpOptions(items); }).catch(() => { /* Optional Go-only decoration. */ });
      api.missingSkills().then((items) => { if (current()) setMissing(items); }).catch(() => { /* Optional Go-only decoration. */ });
    }
    api.skills().then((ss) => {
      if (!current()) return;
      setSkills(ss);
      if (!RESTRICTED_SKILLS) ss.forEach((s) => {
        void api.skillVisibility(s.name)
          .then((ids) => { if (current()) setVisibility((v) => ({ ...v, [s.name]: ids })); })
          .catch(() => { /* Optional Go-only decoration. */ });
      });
    }).catch((error) => { if (current()) setCatalogError((error as Error).message || "Failed to load skills"); })
      .finally(() => { if (current()) setCatalogLoading(false); });
  }, []);

  React.useEffect(() => { load(); }, [load]);

  // ── Upload a .zip skill ───────────────────────────────────────────────────
  async function uploadZip(file: File, overwrite = false) {
    if (rootWritesSuspended()) return;
    setUploading(true);
    try {
      const r = await api.uploadSkill(file, overwrite);
      toast.success(`Installed skill: ${r.name} (${r.files} files)`);
      load();
    } catch (e) {
      const msg = (e as Error).message;
      // offer overwrite when the skill already exists
      if (!overwrite && (msg.includes("\u5df2\u5b58\u5728") || msg.toLowerCase().includes("already exists"))) {
        if (window.confirm(`${msg}\n\nOverwrite the existing skill with this name?`)) {
          await uploadZip(file, true);
          return;
        }
      } else {
        toast.error("Upload failed: " + msg);
      }
    } finally {
      setUploading(false);
    }
  }
  function onUploadPick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = ""; // reset so picking the same file again re-fires
    if (f) uploadZip(f);
  }

  React.useEffect(() => {
    if (creating) {
      // Small timeout so the element is actually in the DOM before focusing
      setTimeout(() => inlineRef.current?.focus(), 20);
    }
  }, [creating]);

  // Sync detail-panel MCP state when the selected skill changes.
  React.useEffect(() => {
    if (!selected || selected.path !== null) return;
    const sk = skills.find((s) => s.name === selected.skill);
    setDetailMcps(sk?.mcps ?? []);
  }, [selected, skills]);

  // Recent calls for the selected skill (detail panel only).
  React.useEffect(() => {
    if (RESTRICTED_SKILLS || !selected || selected.path !== null) { setUsageCalls([]); return; }
    const name = selected.skill;
    setUsageLoading(true);
    api.skillUsage(name, 20)
      .then((calls) => setUsageCalls(calls))
      .catch(() => setUsageCalls([]))
      .finally(() => setUsageLoading(false));
  }, [selected]);

  // Retry is part of the request identity, not an extra ignored effect dependency.
  const readTarget = React.useMemo(() => ({
    skill: selected?.skill, path: selected?.path, retry: fileRetry,
  }), [selected?.skill, selected?.path, fileRetry]);
  React.useEffect(() => {
    if (readTarget.skill == null || readTarget.path == null) return;
    return workspace.read(readTarget.skill, readTarget.path);
  }, [workspace, readTarget]);

  // ── Expand helpers ────────────────────────────────────────────────────────
  function toggleExpanded(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }
  function ensureExpanded(skill: string, dirPath: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.add(skill);
      if (dirPath) {
        let cur = "";
        for (const part of dirPath.split("/")) {
          cur = cur ? `${cur}/${part}` : part;
          next.add(`${skill}:${cur}`);
        }
      }
      return next;
    });
  }

  // ── Inline create ─────────────────────────────────────────────────────────
  const creatingRef = React.useRef<Creating>(null);
  const newEntryRef = React.useRef("");
  function startCreate(skill: string, inDir: string, kind: "file" | "dir") {
    const state = workspace.getSnapshot();
    if (!state.writeModeEnabled || state.rootPending || state.rootUncertain || state.deletionPending || state.deletionUncertain || state.importPending || state.importUncertain || state.pending.has(skill) || state.uncertain.has(skill)) return;
    cancelRef.current = false;
    ensureExpanded(skill, inDir);
    const next = { skill, inDir, kind };
    creatingRef.current = next;
    newEntryRef.current = "";
    setCreating(next);
    setNewEntryName("");
  }

  async function commitCreate(owner: Creating) {
    if (!owner || creatingRef.current !== owner || !workspace.getSnapshot().writeModeEnabled) return;
    if (cancelRef.current) { cancelRef.current = false; return; }
    const c = creatingRef.current;
    const name = newEntryRef.current.trim();
    // Consume this exact input before confirmation/await: Enter plus blur
    // cannot dispatch the same write twice, and later inputs keep ownership.
    creatingRef.current = null;
    newEntryRef.current = "";
    setCreating(null);
    setNewEntryName("");
    if (!c || !name || !alive.current) return;
    const fullPath = c.inDir ? `${c.inDir}/${name}` : name;
    if (c.kind === "file" && !window.confirm(`Write an empty file at ${c.skill}/${fullPath}? If that path already exists, its contents will be replaced. This is not exclusive file creation.`)) return;
    const ok = await workspace.create(c.skill, fullPath, c.kind);
    if (!alive.current || !ok) return;
    toast.success(c.kind === "dir" ? `Folder ready: ${fullPath}` : `Wrote empty file: ${fullPath}`);
    // Do not select the created path: the user may have navigated or edited a
    // different draft. Catalog refresh never hydrates/clears file drafts.
    load();
  }

  function cancelCreate(owner: Creating) {
    if (!owner || creatingRef.current !== owner) return;
    cancelRef.current = true;
    creatingRef.current = null;
    newEntryRef.current = "";
    setCreating(null);
    setNewEntryName("");
  }

  async function deletePath(skill: string, path: string) {
    if (rootWritesSuspended() || pathDeletionBlocked(skill, path)) return false;
    return workspace.remove(skill, path);
  }

  async function deleteSkill(name: string) {
    if (rootWritesSuspended()) return;
    try {
      await api.deleteSkill(name);
      toast.success(`Deleted skill: ${name}`);
      if (selected?.skill === name) setSelected(null);
      load();
    } catch (e) {
      toast.error("Delete failed: " + (e as Error).message);
    }
  }

  async function runPendingDelete() {
    const p = pendingDeleteRef.current;
    if (!p || p !== pendingDelete || !deleteAlive.current || deleteRequest.current) return;
    if (p.kind === "skill" ? RESTRICTED_SKILLS || rootWritesSuspended() : pathDeletionBlocked(p.skill, p.path)) return;
    const owner = {};
    deleteRequest.current = owner;
    setDeleting(true);
    try {
      if (p.kind === "skill") {
        // Existing Go-only whole-skill workflow is not enabled for Python.
        await deleteSkill(p.skill);
      } else {
        const ok = await deletePath(p.skill, p.path);
        if (!ok || !deleteAlive.current || deleteRequest.current !== owner) return;
        toast.success(`Deleted: ${p.skill}/${p.path}. Any cached local text is retained; this is not recoverable host storage.`);
        // Never clear selection: the user may have navigated, or edited while
        // the delete was pending. Deleted drafts stay visible and cannot Save.
        load();
      }
      if (deleteAlive.current && pendingDeleteRef.current === p) setPendingDelete(null);
    } finally {
      if (deleteRequest.current === owner) {
        deleteRequest.current = null;
        if (deleteAlive.current) setDeleting(false);
      }
    }
  }

  async function saveFile() {
    if (!selected || selected.path === null || selectionRef.current !== selected || !alive.current) return;
    const target = selected;
    const ok = await workspace.save(target.skill, target.path);
    if (alive.current && ok) toast.success(`Saved ${target.skill}/${target.path}`);
  }

  async function toggleSkillMcp(skillName: string, mcpName: string, mcpOn: boolean) {
    if (rootWritesSuspended()) return;
    const next = mcpOn
      ? [...detailMcps, mcpName]
      : detailMcps.filter((n) => n !== mcpName);
    setDetailMcps(next);
    try {
      await api.updateSkillMeta(skillName, { mcps: next });
      toast.success(`${mcpOn ? "Linked" : "Unlinked"} ${mcpName}`);
      load();
    } catch (e) {
      // roll back on error
      setDetailMcps(detailMcps);
      toast.error("Operation failed: " + (e as Error).message);
    }
  }

  async function toggleVisibility(skillName: string, agentId: string, agentName: string) {
    if (rootWritesSuspended()) return;
    const on = (visibility[skillName] ?? []).includes(agentId);
    try {
      await api.toggleSkillVisibility(agentId, skillName, !on);
      toast.success(`${on ? "Revoked" : "Granted"} visibility for ${agentName}`);
      const ids = await api.skillVisibility(skillName);
      setVisibility((v) => ({ ...v, [skillName]: ids }));
    } catch (e) {
      toast.error("Operation failed: " + (e as Error).message);
    }
  }

  async function createNewSkill() {
    const state = workspace.getSnapshot();
    if (!alive.current || !newDialog.current.open || newRequest.current || !state.writeModeEnabled || state.rootPending
      || state.rootUncertain || state.deletionUncertain || state.importPending || state.importUncertain || state.pending.size || state.uncertain.size) return;
    const draft = newDraftRef.current;
    const name = draft.name.trim();
    if (!validSkillCreationName(name)) {
      toast.error("Use 1–64 letters/digits/hyphens, starting with a letter; ASCII letters must be lowercase, without trailing or consecutive hyphens; maximum 255 UTF-8 bytes.");
      return;
    }
    if (!draft.description.trim()) { toast.error("Description is required"); return; }
    const dialog = newDialog.current;
    const request = {};
    newRequest.current = request;
    setCreatingSkill(true);
    try {
      const receipt = await workspace.createSkill({
        name, description: draft.description.trim(),
        license: draft.license.trim() || undefined,
        compatibility: draft.compatibility.trim() || undefined,
        instructions: draft.instructions.trim() || undefined,
        ...(!RESTRICTED_SKILLS && draft.mcps.length ? { mcps: [...draft.mcps] } : {}),
      });
      if (!receipt || !alive.current) return;
      toast.success(`Skill created: ${receipt.name}`);
      if (newDraftRef.current === draft) {
        const empty = emptyNewSkillDraft();
        newDraftRef.current = empty;
        setNewDraft(empty);
        if (newDialog.current === dialog) setNewOpen(false);
      }
      // Do not change selected files or their retained drafts after creation.
      load();
      // Primary creation is already announced and visible in the catalog. A
      // Go-only visibility failure (or slow response) is a separate outcome.
      const visibilityResults = RESTRICTED_SKILLS ? [] : await Promise.allSettled(
        draft.visibility.map(async (id) => api.toggleSkillVisibility(id, receipt.name, true)),
      );
      if (alive.current && visibilityResults.some((result) => result.status === "rejected")) {
        toast.error(`Skill ${receipt.name} created, but some visibility updates failed. Review visibility separately; do not repeat creation.`);
      }
    } finally {
      if (newRequest.current === request) {
        newRequest.current = null;
        if (alive.current) setCreatingSkill(false);
      }
    }
  }

  // ── Inline input JSX helper (NOT a React component — avoids remount on re-render) ──
  // Defined as a plain function returning JSX so React never sees a new component type.
  function inlineInputJSX(indent: number) {
    return (
      <div
        key="__inline_create__"
        className="flex items-center gap-1 py-0.5 pr-2"
        style={{ paddingLeft: indent }}
      >
        {creating?.kind === "dir"
          ? <FolderIcon className="size-3.5 shrink-0 text-amber-500" />
          : <FileTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
        }
        <Input
          ref={inlineRef}
          className="h-6 flex-1 px-1 py-0 font-mono text-xs"
          placeholder={creating?.kind === "dir" ? "folder-name" : "filename.py"}
          value={newEntryName}
          onChange={(e) => { if (creatingRef.current === creating) { newEntryRef.current = e.target.value; setNewEntryName(e.target.value); } }}
          onKeyDown={(e) => {
            if (e.key === "Enter") { cancelRef.current = false; void commitCreate(creating); }
            if (e.key === "Escape") cancelCreate(creating);
          }}
          onBlur={() => { void commitCreate(creating); }}
        />
      </div>
    );
  }

  // ── Recursive tree renderer ───────────────────────────────────────────────
  function renderTree(nodes: TreeNode[], skill: string, depth: number): React.ReactNode {
    // depth+1: the skill root sits at 8px (px-2); its children indent one step
    // further in so the tree reads as nested under the skill folder.
    const baseIndent = 8 + (depth + 1) * 14;
    return nodes.map((node) => {
      if (node.type === "dir") {
        const key = `${skill}:${node.path}`;
        const open = expanded.has(key);
        return (
          <div key={node.path}>
            <div
              className="group relative flex cursor-pointer select-none items-center gap-1 rounded py-0.5 pr-1 text-sm hover:bg-muted"
              style={{ paddingLeft: baseIndent }}
              onClick={() => toggleExpanded(key)}
            >
              <ChevronRightIcon className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
              {open
                ? <FolderOpenIcon className="size-3.5 shrink-0 text-amber-500" />
                : <FolderIcon className="size-3.5 shrink-0 text-amber-500" />
              }
              <span className="min-w-0 flex-1 truncate" title={node.path}>{node.name}</span>
              {/* Absolute so a long name can never push the actions out of view */}
              <span className="absolute inset-y-0 right-1 hidden items-center gap-0.5 rounded bg-muted pl-1 group-hover:flex">
                <Button size="icon" variant="ghost" className="size-5" title="Write empty file (may replace existing content)" disabled={rootWriteBlocked || workspaceState.pending.has(skill) || workspaceState.uncertain.has(skill)}
                  onClick={(e) => { e.stopPropagation(); startCreate(skill, node.path, "file"); }}>
                  <FilePlusIcon className="size-3 text-muted-foreground" />
                </Button>
                <Button size="icon" variant="ghost" className="size-5" title="New folder" disabled={rootWriteBlocked || workspaceState.pending.has(skill) || workspaceState.uncertain.has(skill)}
                  onClick={(e) => { e.stopPropagation(); startCreate(skill, node.path, "dir"); }}>
                  <FolderPlusIcon className="size-3 text-muted-foreground" />
                </Button>
                <Button size="icon" variant="ghost" className="size-5" title={RESTRICTED_SKILLS ? "Delete empty folder" : "Delete folder"} disabled={pathDeletionBlocked(skill, node.path)}
                  onClick={(e) => { e.stopPropagation(); setPendingDelete({ kind: "dir", skill, path: node.path }); }}>
                  <Trash2Icon className="size-3 text-destructive" />
                </Button>
              </span>
            </div>
            {open && (
              <>
                {renderTree(node.children, skill, depth + 1)}
                {creating?.skill === skill && creating.inDir === node.path &&
                  inlineInputJSX(baseIndent + 14)}
              </>
            )}
          </div>
        );
      }

      // file node
      const isSelected = selected?.skill === skill && selected.path === node.path;
      return (
        <div
          key={node.path}
          className={cn(
            "group relative flex cursor-pointer select-none items-center gap-1 rounded py-0.5 pr-1 text-sm",
            isSelected ? "bg-accent text-accent-foreground" : "hover:bg-muted",
          )}
          style={{ paddingLeft: baseIndent + 16 }}
          onClick={() => setSelected({ skill, path: node.path })}
        >
          <FileTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate font-mono text-xs" title={node.path}>{node.name}</span>
          <span className={cn(
            "absolute inset-y-0 right-1 hidden items-center rounded pl-1 group-hover:flex",
            isSelected ? "bg-accent" : "bg-muted",
          )}>
            <Button size="icon" variant="ghost" className="size-5"
              title="Delete file" disabled={pathDeletionBlocked(skill, node.path)}
              onClick={(e) => { e.stopPropagation(); setPendingDelete({ kind: "file", skill, path: node.path }); }}>
              <Trash2Icon className="size-3 text-destructive" />
            </Button>
          </span>
        </div>
      );
    });
  }

  const uploadLabel = uploading ? "Uploading…" : "Upload archive";
  const importLabel = MOCK ? "Simulate import" : "Import without replacing";
  const dialogDeleteError = pendingDelete && pendingDelete.kind !== "skill"
    && workspaceState.errors.get(pendingDelete.skill)?.path === pendingDelete.path
    ? workspaceState.errors.get(pendingDelete.skill)?.message : null;
  const dialogDeleteUncertain = workspaceState.deletionUncertain;
  const folderDeleteDescription = RESTRICTED_SKILLS
    ? "Only an empty folder can be deleted. A nonempty folder is refused, not recursively removed. This permanent deletion cannot be undone."
    : "All files in this folder will also be deleted. This cannot be undone.";

  const selectedSkill = selected
    ? skills.find((s) => s.name === selected.skill) ?? null
    : null;

  return (
    <div data-content-padding="false" className="flex flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-2.5 lg:px-6">
        <div className="flex flex-col gap-0.5">
          <h1 className="text-sm font-semibold leading-tight">Skill</h1>
          <p className="text-muted-foreground text-xs">{RESTRICTED_SKILLS ? "Scoped skills workspace · No usage ledger" : "Skill library · agentskills.io specification · Per-agent visibility"}</p>
        </div>
        {/* Missing skills: explicitly requested by agents but absent from the library; identifies useful additions. */}
        {missing.length > 0 && (
          <Popover>
            <PopoverTrigger asChild>
              <Button size="sm" variant="outline" className="ml-auto">
                <AlertTriangleIcon className="size-3.5 text-amber-500" />
                {missing.length} missing calls
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80">
              <p className="mb-2 text-xs text-muted-foreground">
                Skills requested by agents but absent from the library, ranked by request count.
              </p>
              <div className="space-y-1">
                {missing.map((m) => (
                  <div key={m.skill} className="flex items-center gap-2 text-sm">
                    <code className="min-w-0 flex-1 truncate font-mono text-xs" title={m.skill}>{m.skill}</code>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{m.calls} calls</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{fmtTime(m.last_used)}</span>
                  </div>
                ))}
              </div>
            </PopoverContent>
          </Popover>
        )}
      </div>
      {RESTRICTED_SKILLS && <p className="border-b px-4 py-2 text-muted-foreground text-xs">{MOCK ? "Fixture-only Skills demo; no real filesystem host is contacted. Deletion acknowledgements are simulated without host-mode discovery." : "Browse the host-granted skills root and retain local drafts. Saving files, creating skills and creating folders require a confirmed write-enabled host mode; the host still authorizes every request. Deletion needs a separate confirmed host option and supports only individual files or empty folders; required SKILL.md cannot be deleted."} {MOCK ? "ZIP import also uses only fixture acknowledgements, without host discovery." : "Create-only ZIP import needs its own confirmed host option."} Whole-skill deletion, MCP, visibility and usage are unavailable here. A file write replaces existing content; it is not create-only.</p>}
      <div className="flex items-center gap-2 border-b px-4 py-2 text-xs">
        <Button size="sm" variant="outline" onClick={load} disabled={catalogLoading}>Refresh catalog</Button>
        {catalogLoading && <span role="status">Loading catalog…</span>}
        {catalogError && <span role="alert">Catalog unavailable: {catalogError}. Previously loaded entries may be stale.</span>}
      </div>
      {workspaceState.profile && (
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2 text-xs">
          <Button size="sm" variant="outline" onClick={refreshProfile}>Recheck host mode</Button>
          {workspaceState.profile.status === "loading" && <span role="status">Checking Skills host mode; writes are disabled.</span>}
          {workspaceState.profile.status === "unchecked" && <span role="status">Skills host mode has not been confirmed; writes are disabled.</span>}
          {workspaceState.profile.status === "error" && <span role="alert">Host mode unavailable: {workspaceState.profile.error} Writes remain disabled. Local drafts remain available; browsing still depends on host access.</span>}
          {workspaceState.profile.status === "ready" && <span>{workspaceState.profile.writesEnabled ? "Host configured for writes; each request is still checked. Mode refresh cannot clear unconfirmed operations." : "Read-only Skills host. Browsing and local drafts are available; saving and creation are disabled."}</span>}
          {workspaceState.profile.status === "ready" && <span>{workspaceState.importModeEnabled ? "Host separately configured for create-only archive import." : "Archive import disabled; write or delete mode alone does not enable it."}</span>}
          {workspaceState.profile.status === "ready" && <span>{workspaceState.deleteModeEnabled ? "Host separately configured for deletion; every request is still checked." : "File and folder deletion disabled; write mode alone does not enable it."}</span>}
        </div>
      )}
      {workspaceState.importPending && <p role="status" className="px-4 py-2 text-xs">Importing archive {workspaceState.importPending.archiveName}; all new tracked workspace mutations are waiting. Local drafts remain editable.</p>}
      {workspaceState.importError && !workspaceState.importUncertain && <p role="alert" className="px-4 py-2 text-destructive text-xs">Could not import archive {workspaceState.importError.archiveName || "(selected archive)"}: {workspaceState.importError.message}. Reopen Import archive to review the retained selection.</p>}
      {workspaceState.importUncertain && <p role="alert" className="px-4 py-2 text-destructive text-xs">Import of archive {workspaceState.importUncertain.archiveName} is unconfirmed; the installed skill identity is not verified. All tracked workspace mutations are suspended. Do not retry, rename or overwrite to work around this. Keep local drafts and reconcile the filesystem before reopening; catalog and mode refresh do not unlock writes.</p>}
      {workspaceState.deletionPending && <p role="status" className="px-4 py-2 text-xs">Deletion in progress; all new workspace mutations are waiting. Local drafts remain editable.</p>}
      {workspaceState.deletionUncertain && <p role="alert" className="px-4 py-2 text-destructive text-xs">Deletion at {workspaceState.deletionUncertain.skill}/{workspaceState.deletionUncertain.path} is unconfirmed. All workspace mutations are suspended. Do not repeat deletion. Refreshing host mode or the catalog does not unlock writes; keep local drafts and reconcile the filesystem before reopening.</p>}
      {workspaceState.rootPending && <p role="status" className="px-4 py-2 text-muted-foreground text-xs">Creating {workspaceState.rootPending.name}; other workspace writes are waiting.</p>}
      {workspaceState.rootUncertain && (
        <p role="alert" className="px-4 py-2 text-destructive text-xs">Creation of {workspaceState.rootUncertain.name} is unconfirmed. All writes in this Skills workspace are suspended. Do not repeat creation or switch names to retry. Inspect and reconcile the filesystem before reopening the workspace; catalog refresh does not unlock writes. Keep or copy unsaved drafts.</p>
      )}
      {workspaceState.rootError && !workspaceState.rootUncertain && (
        <p role="alert" className="px-4 py-2 text-destructive text-xs">Could not create {workspaceState.rootError.name}: {workspaceState.rootError.message}</p>
      )}
      {[...workspaceState.errors].map(([skill, error]) => (
        <p key={skill} role="alert" className="px-4 py-2 text-destructive text-xs">{skill}/{error.path}: {error.message}</p>
      ))}
      {[...workspaceState.uncertain].map(([skill, operation]) => (
        <p key={skill} role="alert" className="px-4 py-2 text-destructive text-xs">Unconfirmed operation at {skill}/{operation.path}. Further file writes and folder creation for this skill are blocked in this workspace. Do not repeat it. Inspect the filesystem and reconcile the outcome before reopening the workspace; refreshing the catalog does not clear this warning. Keep or copy any unsaved draft.</p>
      ))}
      <div className="flex flex-1 overflow-hidden">
        {/* ── Left file tree ── */}
        <div className="flex w-64 shrink-0 flex-col border-r">
          <div className="flex flex-col gap-2 border-b p-2">
            <Button size="sm" variant="outline" className="w-full" onClick={() => setNewOpen(true)}>
              <PlusIcon className="size-3.5" />New skill
            </Button>
            {!RESTRICTED_SKILLS && <input
              ref={uploadRef}
              type="file"
              accept=".zip,application/zip"
              className="hidden"
              onChange={onUploadPick}
            />}
            <Button
              size="sm"
              variant="outline"
              className="w-full"
              disabled={!RESTRICTED_SKILLS && (rootWriteBlocked || uploading)}
              onClick={() => RESTRICTED_SKILLS ? setImportOpen(true) : uploadRef.current?.click()}
              title={RESTRICTED_SKILLS ? "Import a new skill without replacing existing files" : "Upload a .zip archive containing SKILL.md"}
            >
              <UploadIcon className="size-3.5" />
              {RESTRICTED_SKILLS ? "Import archive" : uploadLabel}
            </Button>
          </div>
          {/* Radix viewport wraps children in a display:table div that grows with
              content — force it to block so long names truncate instead of
              widening the rows past the sidebar. */}
          <ScrollArea className="flex-1 [&>[data-slot=scroll-area-viewport]>div]:!block">
            <div className="p-1">
              {skills.map((s) => {
                const isOpen = expanded.has(s.name);
                const tree = buildTree(s.files);
                const isSkillSelected = selected?.skill === s.name && selected.path === null;
                return (
                  <div key={s.name}>
                    {/* Skill root */}
                    <div
                      className={cn(
                        "group relative flex cursor-pointer select-none items-center gap-1 rounded px-2 py-1 text-sm",
                        isSkillSelected ? "bg-accent text-accent-foreground" : "hover:bg-muted",
                      )}
                      onClick={() => {
                        toggleExpanded(s.name);
                        setSelected({ skill: s.name, path: null });
                      }}
                    >
                      <ChevronRightIcon className={cn("size-3.5 shrink-0 transition-transform", isOpen && "rotate-90")} />
                      {isOpen
                        ? <FolderOpenIcon className="size-3.5 shrink-0 text-blue-500" />
                        : <FolderIcon className="size-3.5 shrink-0 text-blue-500" />
                      }
                      <span className="min-w-0 flex-1 truncate font-semibold" title={s.name}>{s.name}</span>
                      {!RESTRICTED_SKILLS && s.calls > 0 && (
                        <span
                          className="shrink-0 rounded bg-muted px-1 text-[10px] tabular-nums text-muted-foreground"
                          title={`${s.calls} calls · Last ${fmtTime(s.last_used)}`}
                        >
                          {s.calls}
                        </span>
                      )}
                      <span className={cn(
                        "absolute inset-y-0 right-1 hidden items-center gap-0.5 rounded pl-1 group-hover:flex",
                        isSkillSelected ? "bg-accent" : "bg-muted",
                      )}>
                        <Button size="icon" variant="ghost" className="size-5" title="Write empty file (may replace existing content)" disabled={rootWriteBlocked || workspaceState.pending.has(s.name) || workspaceState.uncertain.has(s.name)}
                          onClick={(e) => { e.stopPropagation(); startCreate(s.name, "", "file"); }}>
                          <FilePlusIcon className="size-3 text-muted-foreground" />
                        </Button>
                        <Button size="icon" variant="ghost" className="size-5" title="New folder" disabled={rootWriteBlocked || workspaceState.pending.has(s.name) || workspaceState.uncertain.has(s.name)}
                          onClick={(e) => { e.stopPropagation(); startCreate(s.name, "", "dir"); }}>
                          <FolderPlusIcon className="size-3 text-muted-foreground" />
                        </Button>
                        <Button size="icon" variant="ghost" className="size-5" title="Delete skill" disabled={RESTRICTED_SKILLS || rootWriteBlocked}
                          onClick={(e) => { e.stopPropagation(); setPendingDelete({ kind: "skill", skill: s.name }); }}>
                          <Trash2Icon className="size-3 text-destructive" />
                        </Button>
                      </span>
                    </div>

                    {/* Expanded recursive file tree */}
                    {isOpen && (
                      <>
                        {renderTree(tree, s.name, 0)}
                        {creating?.skill === s.name && creating.inDir === "" &&
                          inlineInputJSX(22)}
                      </>
                    )}
                  </div>
                );
              })}

              {skills.length === 0 && !catalogLoading && !catalogError && (
                <p className="p-3 text-muted-foreground text-xs">{RESTRICTED_SKILLS ? "No skills in the granted root." : "No skills yet. Click New to begin."}</p>
              )}
            </div>
          </ScrollArea>
        </div>

        {/* ── Right panel ── */}
        <div className="flex flex-1 flex-col overflow-auto p-4">
          {!selected && !RESTRICTED_SKILLS && !catalogError && !catalogLoading && (
            <SkillsOverview
              skills={skills}
              missing={missing}
              onSelect={(name) => setSelected({ skill: name, path: null })}
            />
          )}

          {!selected && RESTRICTED_SKILLS && <p className="text-muted-foreground text-sm">Select a skill or file. Drafts are retained while this page stays mounted; save or copy them before leaving. Usage statistics are not available from this scoped filesystem workspace.</p>}
          {selected && selected.path === null && selectedSkill && (
            <div className="max-w-5xl space-y-5">
              <div>
                <h2 className="font-mono text-base font-semibold">{selectedSkill.name}</h2>
                {selectedSkill.description && (
                  <p className="mt-1 text-sm text-muted-foreground">{selectedSkill.description}</p>
                )}
                <div className="mt-2 flex flex-wrap gap-2">
                  {selectedSkill.license && (
                    <Badge variant="outline" className="text-xs font-normal">License: {selectedSkill.license}</Badge>
                  )}
                  {selectedSkill.compatibility && (
                    <Badge variant="secondary" className="text-xs font-normal">{selectedSkill.compatibility}</Badge>
                  )}
                </div>
              </div>

              {/* Two columns: primary configuration (MCP/visibility) on the left, usage statistics on the right.
                  Below lg, flex-row-reverse falls back to a single column. Statistics come first in DOM order,
                  so narrow screens place them above configuration, preserving the previous order. */}
              {!RESTRICTED_SKILLS && <div className="flex flex-col gap-6 lg:flex-row-reverse lg:items-start">
                {/* ── Right: usage statistics ── */}
                <div className="space-y-2 lg:w-80 lg:shrink-0">
                  <Label className="text-xs text-muted-foreground">Usage statistics</Label>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="rounded-md border p-2">
                      <p className="text-lg font-semibold tabular-nums">{selectedSkill.calls}</p>
                      <p className="text-xs text-muted-foreground">Total calls</p>
                    </div>
                    <div className="rounded-md border p-2">
                      <p className="text-lg font-semibold tabular-nums">{selectedSkill.tasks}</p>
                      <p className="text-xs text-muted-foreground">Tasks covered</p>
                    </div>
                    <div className="rounded-md border p-2">
                      <p className="truncate text-sm font-medium" title={fmtTime(selectedSkill.last_used)}>
                        {fmtTime(selectedSkill.last_used)}
                      </p>
                      <p className="text-xs text-muted-foreground">Recent calls</p>
                    </div>
                  </div>
                  {selectedSkill.usage_agents.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1">
                      <span className="text-xs text-muted-foreground">Caller: </span>
                      {selectedSkill.usage_agents.map((k) => (
                        <Badge key={k} variant="secondary" className="text-xs font-normal">{k}</Badge>
                      ))}
                    </div>
                  )}
                  {usageLoading ? (
                    <p className="text-xs text-muted-foreground">Loading call details…</p>
                  ) : usageCalls.length > 0 ? (
                    <div className="rounded-md border">
                      <div className="border-b px-2 py-1 text-xs text-muted-foreground">Last {usageCalls.length} calls</div>
                      <div className="max-h-56 overflow-y-auto">
                        {usageCalls.map((c, i) => (
                          <div key={`${c.ts}-${i}`} className="flex items-center gap-2 border-b px-2 py-1 text-xs last:border-b-0">
                            <span className="tabular-nums text-muted-foreground">{fmtTime(c.ts)}</span>
                            <Badge variant="outline" className="font-normal">{c.agent_key || "—"}</Badge>
                            <span className="ml-auto text-muted-foreground">
                              {c.task_id > 0 ? `Task #${c.task_id}` : c.session_id ? "Chat session" : "—"}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">No calls recorded yet.</p>
                  )}
                </div>

                {/* ── Left: MCP links and visibility ── */}
                <div className="space-y-5 lg:min-w-0 lg:flex-1">
                  <div className="space-y-2">
                    <Label className="text-xs text-muted-foreground">
                      Linked MCP
                      <span className="ml-1 font-normal">(Tools are exposed and unlocked only when the skill loads)</span>
                    </Label>
                    {mcpOptions.length === 0 ? (
                      <p className="text-xs text-muted-foreground">No MCP servers yet. Add one on the MCP page.</p>
                    ) : (
                      <div className="flex flex-wrap gap-x-4 gap-y-2">
                        {mcpOptions.map((m) => (
                          <label key={m.id} className="flex cursor-pointer items-center gap-2 text-sm">
                            <Checkbox
                              checked={detailMcps.includes(m.name)}
                              disabled={rootWriteBlocked}
                              onCheckedChange={(on) => toggleSkillMcp(selectedSkill.name, m.name, !!on)}
                            />
                            {m.name}
                          </label>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="space-y-2">
                    <Label className="text-xs text-muted-foreground">Visibility (per-agent access)</Label>
                    <div className="space-y-2">
                      {agents.map((a) => (
                        <label key={a.key} className="flex cursor-pointer items-center gap-2 text-sm">
                          <Checkbox
                            checked={(visibility[selectedSkill.name] ?? []).includes(a.id)}
                            disabled={rootWriteBlocked}
                            onCheckedChange={() => toggleVisibility(selectedSkill.name, a.id, a.name)}
                          />
                          {a.name}
                        </label>
                      ))}
                      {agents.length === 0 && (
                        <span className="text-xs text-muted-foreground">(No agents yet)</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>}
            </div>
          )}

          {selected && selected.path !== null && (
            <div className="flex h-full flex-col gap-2">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{selected.skill}</span>
                <span>/</span>
                <span className="font-mono">{selected.path}</span>
                <Button size="sm" className="ml-auto" onClick={saveFile} disabled={!fileDraft?.loaded || !fileDraft.dirty || fileDraft.deleted || saving || blocked}>
                  {saving ? "Saving…" : "Save"}
                </Button>
              </div>
              {fileDraft?.deleted && <p role="status" className="text-destructive text-xs">This path was deleted on the host. Cached text is retained only in this page; copy anything needed before leaving. Save and recreation are disabled until you reconcile and reopen the workspace.</p>}
              {fileDraft?.dirty && <p className="text-muted-foreground text-xs">Unsaved draft retained for this file until you leave this page.</p>}
              {fileDraft?.error && (
                <div role="alert" className="text-destructive text-sm">Read failed: {fileDraft.error} <Button variant="outline" onClick={() => setFileRetry((n) => n + 1)}>Retry read</Button></div>
              )}
              {!fileDraft?.error && fileLoading && <p className="text-muted-foreground text-xs">Loading…</p>}
              {!fileDraft?.error && !fileLoading && (
                <Textarea
                  className="flex-1 resize-none font-mono text-xs"
                  value={fileDraft?.content ?? ""}
                  onChange={(e) => { if (selected.path !== null && selectionRef.current === selected) workspace.edit(selected.skill, selected.path, e.target.value); }}
                />
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Deletion confirmation ── */}
      <AlertDialog open={!!pendingDelete} onOpenChange={(o) => { if (!o) setPendingDelete(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingDelete?.kind === "skill" && `Delete skill“${pendingDelete.skill}”?`}
              {pendingDelete?.kind === "dir" && `Delete folder “${pendingDelete.skill}/${pendingDelete.path}”?`}
              {pendingDelete?.kind === "file" && `Delete file “${pendingDelete.skill}/${pendingDelete.path}”?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete?.kind === "skill"
                ? "All skill files, MCP links and visibility settings will be deleted. This cannot be undone."
                : pendingDelete?.kind === "dir"
                  ? folderDeleteDescription
                  : "This permanently deletes the named file. This cannot be undone. Unsaved local drafts block deletion; cached text is not a backup."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {dialogDeleteError && <p role="alert" className="text-destructive text-sm">{pendingDelete?.skill}/{pendingDelete && pendingDelete.kind !== "skill" ? pendingDelete.path : ""}: {dialogDeleteError}</p>}
          {dialogDeleteUncertain && <p role="alert" className="text-destructive text-sm">The earlier deletion of {dialogDeleteUncertain.skill}/{dialogDeleteUncertain.path} is unconfirmed. All workspace mutations are suspended, including this dialog's target. Do not retry; keep local drafts and reconcile the filesystem first.</p>}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleting || !pendingDelete || (pendingDelete.kind === "skill" ? RESTRICTED_SKILLS || rootWriteBlocked : pathDeletionBlocked(pendingDelete.skill, pendingDelete.path))}
              onClick={(e) => { e.preventDefault(); void runPendingDelete(); }}
            >
              {deleting ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Python-only create-only archive import; Go's picker/overwrite flow stays separate. */}
      {RESTRICTED_SKILLS && <Sheet open={importOpen} onOpenChange={setImportOpen}>
        <SheetContent side="right" className="flex w-full flex-col gap-4 data-[side=right]:sm:max-w-xl">
          <SheetHeader><SheetTitle>Import new skill archive</SheetTitle><SheetDescription>{MOCK ? "Simulate a create-only archive acknowledgement." : "Install a new skill from a supported ZIP without replacing an existing skill."}</SheetDescription></SheetHeader>
          <div className="space-y-3 px-4 text-sm">
            <p>{MOCK ? "Fixture-only acknowledgement; no archive is installed or inspected." : "Create-only import into the host-granted root. Existing skills are never overwritten. The host validates the ZIP and derives the installed name from SKILL.md, not this filename."}</p>
            <p className="text-muted-foreground text-xs">Archive at most 1 MiB; inflated total at most 1 MiB, each file at most 256 KiB, at most 128 entries. Bounded Store/Deflate subset only, one SKILL.md with an explicit valid name and nonblank description. Regular files count in the receipt; folders also count toward the entry limit. A file-picker filter is not archive validation.</p>
            <p className="text-muted-foreground text-xs">Standard Info-ZIP archives within this subset may include matching timestamp and UID/GID fields; these are not restored. Not supported: other extra fields, streamed/data-descriptor ZIPs, ZIP64, encryption, comments, preamble/trailer data, non-ASCII names without a UTF-8 flag or MCP metadata. Partial effects cannot be assumed rolled back if an import outcome is unknown.</p>
            <Label htmlFor="skill-import-file">ZIP archive</Label>
            <Input id="skill-import-file" type="file" accept=".zip,application/zip" onChange={(e) => pickImportFile(e, importSession)} />
            {importFile && <p className="break-all">Selected: {importFile.name} ({importFile.size} bytes). Kept until this page closes or an unchanged import is acknowledged.</p>}
            {!workspaceState.importModeEnabled && <p role="status">Import is disabled until a compatible host explicitly confirms import mode. Existing local drafts are unchanged.</p>}
            {workspaceState.importPending && <p role="status">Importing {workspaceState.importPending.archiveName}. Do not repeat this request.</p>}
            {workspaceState.importError?.file === importFile && <p role="alert" className="text-destructive">{workspaceState.importError?.archiveName}: {workspaceState.importError?.message}</p>}
            {workspaceState.importUncertain && <p role="alert" className="text-destructive">The earlier import of {workspaceState.importUncertain.archiveName} is unconfirmed. All tracked mutations are suspended, even if a different archive is selected. Do not retry or overwrite; reconcile the filesystem first.</p>}
          </div>
          <SheetFooter>
            <Button variant="outline" onClick={() => setImportOpen(false)}>Close</Button>
            <Button onClick={importArchive} disabled={importing || !importFile || !workspaceState.importModeEnabled || rootWriteBlocked || workspaceState.pending.size > 0 || workspaceState.uncertain.size > 0}>{importing ? "Importing…" : importLabel}</Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>}

      {/* ── New skill dialog ── */}
      <Sheet open={newOpen} onOpenChange={setNewOpen}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 data-[side=right]:sm:max-w-xl">
          <SheetHeader className="px-4 pt-4">
            <SheetTitle>New skill</SheetTitle>
          </SheetHeader>
          {RESTRICTED_SKILLS && <p className="px-4 pt-2 text-muted-foreground text-xs">Creates a new folder and SKILL.md under the granted root, without replacing an existing skill. Metadata must be single-line. MCP links and visibility are unavailable.</p>}
          {!workspaceState.writeModeEnabled && <p role="status" className="px-4 pt-2 text-muted-foreground text-xs">Creation is disabled until this Skills host confirms write-enabled mode. You can keep or copy this draft.</p>}
          {workspaceState.rootUncertain && <p role="alert" className="px-4 pt-2 text-destructive text-xs">Creation is unconfirmed. All workspace writes are suspended; do not repeat it. Your draft is kept.</p>}
          {workspaceState.rootError && !workspaceState.rootUncertain && <p role="alert" className="px-4 pt-2 text-destructive text-xs">{workspaceState.rootError.message}</p>}
          {workspaceState.uncertain.size > 0 && !workspaceState.rootUncertain && <p role="alert" className="px-4 pt-2 text-destructive text-xs">Reconcile existing unconfirmed file operations before creating a new skill. Catalog refresh does not unlock creation.</p>}
          {workspaceState.pending.size > 0 && <p role="status" className="px-4 pt-2 text-muted-foreground text-xs">Wait for the current file or folder write to finish before creating a skill.</p>}
          <Tabs defaultValue="basic" className="flex min-h-0 flex-1 flex-col">
            <TabsList className="mx-4 mt-3 shrink-0 justify-start">
              <TabsTrigger value="basic">Basic information</TabsTrigger>
              {!RESTRICTED_SKILLS && <TabsTrigger value="mcp">
                Linked MCP
                {newMcps.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-primary px-1.5 py-0.5 text-[10px] leading-none text-primary-foreground">
                    {newMcps.length}
                  </span>
                )}
              </TabsTrigger>}
              {!RESTRICTED_SKILLS && <TabsTrigger value="visibility">
                Visibility
                {newVisibility.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-primary px-1.5 py-0.5 text-[10px] leading-none text-primary-foreground">
                    {newVisibility.length}
                  </span>
                )}
              </TabsTrigger>}
            </TabsList>

            {/* Basic information */}
            <TabsContent value="basic" className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4 pt-4 data-[state=inactive]:hidden">
              <div className="grid gap-1.5">
                <Label htmlFor="sk-name">Name <span className="text-destructive">*</span></Label>
                <Input id="sk-name" placeholder="sqli-deepdive" value={newName} onChange={(e) => editNewSkill({ name: e.target.value })} />
                <p className="text-muted-foreground text-xs">1–64 letters, digits and hyphens; start with a letter. ASCII letters must be lowercase; no trailing or consecutive hyphens.</p>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="sk-desc">Description <span className="text-destructive">*</span></Label>
                <Textarea id="sk-desc" rows={2} className="resize-none"
                  placeholder="What this skill does and when to use it."
                  value={newDesc} onChange={(e) => editNewSkill({ description: e.target.value })} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="grid gap-1.5">
                  <Label className="text-muted-foreground text-xs">license</Label>
                  <Input placeholder="MIT / Proprietary" value={newLicense} onChange={(e) => editNewSkill({ license: e.target.value })} />
                </div>
                <div className="grid gap-1.5">
                  <Label className="text-muted-foreground text-xs">compatibility</Label>
                  <Input placeholder="Requires sqlmap and python3" value={newCompat} onChange={(e) => editNewSkill({ compatibility: e.target.value })} />
                </div>
              </div>
              <div className="flex min-h-0 flex-1 flex-col gap-1.5">
                <Label htmlFor="sk-inst">Body <span className="text-muted-foreground text-xs font-normal">(Leave empty to generate a skeleton)</span></Label>
                <Textarea id="sk-inst"
                  className="min-h-40 flex-1 resize-none font-mono text-sm leading-relaxed"
                  placeholder={"## Procedure\n\n1. Check for errors first\n2. Identify the blind-injection type\n\nPlace scripts in scripts/."}
                  value={newInst} onChange={(e) => editNewSkill({ instructions: e.target.value })} />
              </div>
            </TabsContent>

            {/* Linked MCP */}
            {!RESTRICTED_SKILLS && <TabsContent value="mcp" className="overflow-y-auto px-4 pb-4 pt-4 data-[state=inactive]:hidden">
              <p className="mb-3 text-xs text-muted-foreground">Selected MCP tools are exposed and unlocked only when the skill loads.</p>
              {mcpOptions.length === 0 ? (
                <p className="text-xs text-muted-foreground">No MCP servers yet. Add one on the MCP page.</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {mcpOptions.map((m) => (
                    <label key={m.id} className="flex cursor-pointer items-center gap-2 text-sm">
                      <Checkbox
                        checked={newMcps.includes(m.name)}
                        onCheckedChange={(on) =>
                          editNewSkill({ mcps: on ? [...newDraftRef.current.mcps, m.name] : newDraftRef.current.mcps.filter((n) => n !== m.name) })
                        }
                      />
                      {m.name}
                    </label>
                  ))}
                </div>
              )}
            </TabsContent>}

            {/* Visibility */}
            {!RESTRICTED_SKILLS && <TabsContent value="visibility" className="overflow-y-auto px-4 pb-4 pt-4 data-[state=inactive]:hidden">
              <p className="mb-3 text-xs text-muted-foreground">Selected agents can see this skill after creation.</p>
              {agents.length === 0 ? (
                <p className="text-xs text-muted-foreground">(No agents yet)</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {agents.map((a) => (
                    <label key={a.key} className="flex cursor-pointer items-center gap-2 text-sm">
                      <Checkbox
                        checked={newVisibility.includes(a.id)}
                        onCheckedChange={(on) =>
                          editNewSkill({ visibility: on ? [...newDraftRef.current.visibility, a.id] : newDraftRef.current.visibility.filter((id) => id !== a.id) })
                        }
                      />
                      {a.name}
                    </label>
                  ))}
                </div>
              )}
            </TabsContent>}
          </Tabs>

          <SheetFooter className="flex-row justify-end gap-2 border-t px-4 py-3">
            <Button variant="outline" onClick={() => setNewOpen(false)}>Cancel</Button>
            <Button onClick={createNewSkill} disabled={creatingSkill || rootWriteBlocked || workspaceState.pending.size > 0 || workspaceState.uncertain.size > 0}>{creatingSkill ? "Creating…" : "Create"}</Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  );
}
