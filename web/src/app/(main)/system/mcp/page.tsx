"use client";

import * as React from "react";
import { toast } from "sonner";
import { PlusIcon, RefreshCwIcon, ServerIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { MCPServer, MCPTool, Agent } from "@/lib/types";

type Transport = "stdio" | "http" | "sse";
type FormState = {
  name: string;
  transport: Transport;
  command: string;
  args: string;
  url: string;
  env: string;
  insecure: boolean;
};
const emptyForm: FormState = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  url: "",
  env: "",
  insecure: false,
};

export default function MCPPage() {
  const [servers, setServers] = React.useState<MCPServer[]>([]);
  const [agents, setAgents] = React.useState<Agent[]>([]);
  const [visibility, setVisibility] = React.useState<Record<number, string[]>>({});

  const [open, setOpenState] = React.useState(false);
  const [editing, setEditing] = React.useState<MCPServer | null>(null); // null = add mode
  const [tab, setTab] = React.useState<"config" | "tools">("config");
  const [form, setForm] = React.useState<FormState>(emptyForm);
  const [saving, setSaving] = React.useState(false);
  const [tools, setTools] = React.useState<MCPTool[]>([]);
  const [toolsLoading, setToolsLoading] = React.useState(false);
  const [refreshing, setRefreshing] = React.useState(false);
  const [busyIds, setBusyIds] = React.useState<Set<number>>(new Set());
  const active = React.useRef(false);
  const lifetime = React.useRef(0);
  const catalogRequest = React.useRef<object | null>(null);
  const serverSnapshot = React.useRef<MCPServer[]>([]);
  const visibilitySnapshot = React.useRef<Record<number, string[]>>({});
  type Mutation = { id: number; kind: string; lifetime: number };
  type Session = { editing: MCPServer | null; form: FormState; save: object | null; read: object | null; refresh: object | null };
  const sessionRef = React.useRef<Session | null>(null);
  const mutations = React.useRef(new Map<number, Mutation>());

  React.useLayoutEffect(() => {
    active.current = true;
    lifetime.current++;
    setOpenState(false);
    setSaving(false);
    setRefreshing(false);
    setToolsLoading(false);
    setBusyIds(new Set(mutations.current.keys()));
    return () => {
      active.current = false;
      catalogRequest.current = null;
      sessionRef.current = null;
    };
  }, []);

  const load = React.useCallback(() => {
    if (!active.current) return;
    const request = {};
    catalogRequest.current = request;
    const owns = () => active.current && catalogRequest.current === request;
    api.agents().then((items) => { if (owns()) setAgents(items); }).catch(() => {
      // Keep the last successful agent list on a read failure.
    });
    api
      .mcpServers()
      .then((ss) => {
        if (!owns()) return;
        serverSnapshot.current = ss;
        setServers(ss);
        ss.forEach((s) => {
          api
            .resourceVisibility("mcp", s.id)
            .then((ids) => {
              if (!owns()) return;
              visibilitySnapshot.current = { ...visibilitySnapshot.current, [s.id]: ids };
              setVisibility(visibilitySnapshot.current);
            })
            .catch(() => {
              // Keep the last known visibility; a failed read grants nothing.
            });
        });
      })
      .catch(() => {
        // Catalog read failures must not discard current cards or editor drafts.
      });
  }, []);
  React.useEffect(() => {
    load();
  }, [load]);

  function setOpen(next: boolean) {
    if (!active.current) return;
    if (!next) {
      sessionRef.current = null;
      setSaving(false);
      setRefreshing(false);
      setToolsLoading(false);
    }
    setOpenState(next);
  }

  function beginMutation(id: number, kind: string): Mutation | null {
    if (!active.current || mutations.current.has(id) || !serverSnapshot.current.some((s) => s.id === id)) return null;
    const owner = { id, kind, lifetime: lifetime.current };
    mutations.current.set(id, owner);
    setBusyIds(new Set(mutations.current.keys()));
    return owner;
  }

  function ownsMutation(owner: Mutation) {
    return active.current && lifetime.current === owner.lifetime && mutations.current.get(owner.id) === owner;
  }

  function endMutation(owner: Mutation) {
    if (mutations.current.get(owner.id) !== owner) return;
    mutations.current.delete(owner.id);
    if (active.current) setBusyIds(new Set(mutations.current.keys()));
  }

  function updateServer(id: number, patch: Partial<MCPServer>) {
    serverSnapshot.current = serverSnapshot.current.map((s) => s.id === id ? { ...s, ...patch } : s);
    setServers(serverSnapshot.current);
    const session = sessionRef.current;
    if (session?.editing?.id === id) {
      session.editing = { ...session.editing, ...patch };
      setEditing(session.editing);
    }
  }

  function setF(patch: Partial<FormState>) {
    const session = sessionRef.current;
    if (!active.current || !session || (!session.editing && session.save)) return;
    session.form = { ...session.form, ...patch };
    setForm(session.form);
  }

  function parseEnv(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const idx = trimmed.indexOf("=");
      if (idx > 0) {
        const key = trimmed.slice(0, idx).trim();
        if (key) out[key] = trimmed.slice(idx + 1).trim();
      }
    }
    return out;
  }
  function envToText(env: Record<string, string> | undefined): string {
    return Object.entries(env ?? {})
      .map(([k, v]) => `${k}=${v}`)
      .join("\n");
  }

  function openAdd() {
    if (!active.current) return;
    sessionRef.current = { editing: null, form: { ...emptyForm }, save: null, read: null, refresh: null };
    setEditing(null);
    setForm(sessionRef.current.form);
    setTools([]);
    setSaving(false);
    setRefreshing(false);
    setToolsLoading(false);
    setTab("config");
    setOpen(true);
  }

  function openEdit(selected: MCPServer) {
    if (!active.current || mutations.current.has(selected.id)) return;
    const s = serverSnapshot.current.find((item) => item.id === selected.id);
    if (!s) return;
    const draft: FormState = {
      name: s.name, transport: s.transport, command: s.command ?? "", args: (s.args ?? []).join(" "),
      url: s.url ?? "", env: envToText(s.env), insecure: s.insecure ?? false,
    };
    sessionRef.current = { editing: s, form: draft, save: null, read: null, refresh: null };
    setEditing(s);
    setForm(draft);
    setTools([]);
    setSaving(false);
    setRefreshing(false);
    setTab("config");
    setOpen(true);
    void loadTools(s.id);
  }

  async function loadTools(id: number) {
    const session = sessionRef.current;
    if (!active.current || session?.editing?.id !== id || session.refresh) return;
    const request = {};
    session.read = request;
    const owns = () => active.current && sessionRef.current === session && session.read === request;
    setToolsLoading(true);
    try {
      const items = await api.mcpTools(id);
      if (owns()) setTools(items);
    } catch {
      if (owns()) setTools([]);
    } finally {
      if (owns()) {
        session.read = null;
        setToolsLoading(false);
      }
    }
  }

  async function saveForm() {
    const session = sessionRef.current;
    if (!active.current || !session || session.save) return;
    const form = session.form;
    const editing = session.editing;
    if (!form.name.trim()) {
      toast.error("Enter a name");
      return;
    }
    if (form.transport === "stdio" && !form.command.trim()) {
      toast.error("Enter a command");
      return;
    }
    if (form.transport !== "stdio" && !form.url.trim()) {
      toast.error("Enter a remote URL");
      return;
    }
    const mutation = editing ? beginMutation(editing.id, "save") : null;
    if (editing && !mutation) return;
    const owner = mutation ?? {};
    const started = lifetime.current;
    session.save = owner;
    session.read = null;
    setToolsLoading(false);
    setSaving(true);
    const owns = () => active.current && lifetime.current === started && sessionRef.current === session && session.save === owner;
    try {
      const base =
        form.transport !== "stdio"
          ? {
              transport: form.transport,
              url: form.url.trim(),
              command: "",
              args: [] as string[],
              env: parseEnv(form.env), // In remote mode, env contains request headers
              insecure: form.insecure,
            }
          : {
              transport: "stdio" as const,
              command: form.command.trim(),
              args: form.args.trim() ? form.args.trim().split(/\s+/) : [],
              env: parseEnv(form.env),
              insecure: false,
            };
      const payload = {
        ...(editing ? { id: editing.id } : {}),
        name: form.name.trim(),
        enabled: editing ? (serverSnapshot.current.find((s) => s.id === editing.id)?.enabled ?? editing.enabled) : true,
        ...base,
      };
      await api.saveMcpServer(payload);
      if (active.current && lifetime.current === started) {
        if (editing) updateServer(editing.id, payload);
        load();
      }
      if (!owns()) return;
      toast.success(editing ? `Saved${session.form !== form ? "; newer edits remain unsaved" : ""}` : "MCP server added");
      if (!editing) setOpen(false);
    } catch (e) {
      if (owns()) toast.error("Save failed: " + (e as Error).message);
    } finally {
      if (owns()) setSaving(false);
      if (session.save === owner) session.save = null;
      if (mutation) endMutation(mutation);
    }
  }

  async function refreshTools() {
    const session = sessionRef.current;
    if (!active.current || !session?.editing || session.refresh) return;
    const owner = beginMutation(session.editing.id, "refresh");
    if (!owner) return;
    session.refresh = owner;
    session.read = null;
    setToolsLoading(false);
    const owns = () => ownsMutation(owner) && sessionRef.current === session && session.refresh === owner;
    setRefreshing(true);
    try {
      const t = await api.refreshMcpServer(owner.id);
      if (ownsMutation(owner)) {
        updateServer(owner.id, { tools: t.map((item) => item.name) });
        load();
      }
      if (!owns()) return;
      setTools(t);
      toast.success(`Discovered ${t.length} tools`);
    } catch (e) {
      if (owns()) toast.error("Refresh failed: " + (e as Error).message);
    } finally {
      if (owns()) setRefreshing(false);
      if (session.refresh === owner) session.refresh = null;
      endMutation(owner);
    }
  }

  async function removeServer(s: MCPServer) {
    const owner = beginMutation(s.id, "remove");
    if (!owner) return;
    try {
      await api.deleteMcpServer(s.id);
      if (!ownsMutation(owner)) return;
      serverSnapshot.current = serverSnapshot.current.filter((item) => item.id !== s.id);
      setServers(serverSnapshot.current);
      toast.success(`Deleted: ${s.name}`);
      if (sessionRef.current?.editing?.id === s.id) setOpen(false);
      load();
    } catch (e) {
      if (ownsMutation(owner)) toast.error("Delete failed: " + (e as Error).message);
    } finally {
      endMutation(owner);
    }
  }

  async function toggleEnabled(s: MCPServer) {
    const current = serverSnapshot.current.find((item) => item.id === s.id);
    if (!current) return;
    const owner = beginMutation(s.id, "enabled");
    if (!owner) return;
    const enabled = !current.enabled;
    try {
      await api.saveMcpServer({ ...current, enabled });
      if (!ownsMutation(owner)) return;
      updateServer(s.id, { enabled });
      load();
    } catch (e) {
      if (ownsMutation(owner)) toast.error("Operation failed: " + (e as Error).message);
    } finally {
      endMutation(owner);
    }
  }

  async function toggleVisibility(serverId: number, agentId: string, agentName: string) {
    const owner = beginMutation(serverId, "visibility");
    if (!owner) return;
    const on = (visibilitySnapshot.current[serverId] ?? []).includes(agentId);
    try {
      await api.toggleVisibility(agentId, "mcp", serverId, !on);
      if (!ownsMutation(owner)) return;
      const ids = visibilitySnapshot.current[serverId] ?? [];
      visibilitySnapshot.current = { ...visibilitySnapshot.current, [serverId]: on ? ids.filter((id) => id !== agentId) : [...new Set([...ids, agentId])] };
      setVisibility(visibilitySnapshot.current);
      toast.success(`${on ? "Revoked" : "Granted"} visibility for ${agentName}`);
      load();
    } catch (e) {
      if (ownsMutation(owner)) toast.error("Operation failed: " + (e as Error).message);
    } finally {
      endMutation(owner);
    }
  }

  function renderForm() {
    return (
      <fieldset disabled={!editing && saving} className="grid min-w-0 gap-4 py-4">
        <div className="grid gap-2">
          <Label>Transport</Label>
          <div className="flex gap-2">
            <Button
              type="button"
              variant={form.transport === "stdio" ? "default" : "outline"}
              onClick={() => setF({ transport: "stdio" })}
            >
              stdio (local)
            </Button>
            <Button
              type="button"
              variant={form.transport === "http" ? "default" : "outline"}
              onClick={() => setF({ transport: "http" })}
            >
              http (remote)
            </Button>
            <Button
              type="button"
              variant={form.transport === "sse" ? "default" : "outline"}
              onClick={() => setF({ transport: "sse" })}
            >
              sse (legacy)
            </Button>
          </div>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="m-name">Name</Label>
          <Input
            id="m-name"
            placeholder="filesystem"
            value={form.name}
            onChange={(e) => setF({ name: e.target.value })}
          />
        </div>
        {form.transport === "stdio" ? (
          <>
            <div className="grid gap-2">
              <Label htmlFor="m-cmd">Command</Label>
              <Input
                id="m-cmd"
                className="font-mono"
                placeholder="npx"
                value={form.command}
                onChange={(e) => setF({ command: e.target.value })}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="m-args">Arguments (space-separated)</Label>
              <Input
                id="m-args"
                className="font-mono"
                placeholder="-y @modelcontextprotocol/server-filesystem /data"
                value={form.args}
                onChange={(e) => setF({ args: e.target.value })}
              />
            </div>
          </>
        ) : (
          <div className="grid gap-2">
            <Label htmlFor="m-url">Remote URL</Label>
            <Input
              id="m-url"
              className="font-mono"
              placeholder="https://mcp.example.com/mcp"
              value={form.url}
              onChange={(e) => setF({ url: e.target.value })}
            />
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.insecure}
                onCheckedChange={(v) => setF({ insecure: v === true })}
              />
              Skip TLS certificate verification (self-signed certificates)
            </label>
          </div>
        )}
        <div className="grid gap-2">
          <Label htmlFor="m-env">
            {form.transport !== "stdio"
              ? "Headers (KEY=VALUE per line, e.g. Authorization=Bearer xxx)"
              : "Environment variables (KEY=VALUE per line)"}
          </Label>
          <Textarea
            id="m-env"
            className="font-mono"
            placeholder={
              form.transport !== "stdio" ? "Authorization=Bearer xxxx" : "API_KEY=xxxx\nFOO=bar"
            }
            value={form.env}
            onChange={(e) => setF({ env: e.target.value })}
          />
        </div>
      </fieldset>
    );
  }

  function renderTools() {
    return (
      <div className="flex flex-col gap-3 py-4">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground text-sm">{tools.length} tools</span>
          <Button size="sm" variant="outline" disabled={refreshing || (editing !== null && busyIds.has(editing.id))} onClick={refreshTools}>
            <RefreshCwIcon className={refreshing ? "animate-spin" : ""} /> Refresh
          </Button>
        </div>
        {toolsLoading ? (
          <p className="text-muted-foreground text-sm">Loading…</p>
        ) : tools.length === 0 ? (
          <p className="text-muted-foreground text-sm">No tools discovered. Click Refresh to try again.</p>
        ) : (
          <div className="flex flex-col divide-y">
            {tools.map((t) => (
              <div key={t.name} className="py-2.5">
                <code className="font-mono text-sm">{t.name}</code>
                {t.description && (
                  <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
                    {t.description}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-4 md:gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">MCP</h1>
        <p className="text-muted-foreground text-sm">External MCP tool servers · Visibility granted per agent</p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <button
          type="button"
          onClick={openAdd}
          className="text-foreground/70 border-foreground/70 hover:bg-muted/60 hover:shadow-sm flex min-h-[116px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed transition"
        >
          <PlusIcon className="size-6" />
          <span className="text-sm">Add MCP</span>
        </button>

        {servers.map((s) => (
          <Card
            key={s.id}
            onClick={() => openEdit(s)}
            className="hover:border-primary/60 cursor-pointer gap-3 transition hover:shadow-sm"
          >
            <CardHeader>
              <div className="flex items-center gap-2">
                <ServerIcon className="text-muted-foreground size-4 shrink-0" />
                <CardTitle className="truncate text-base">{s.name}</CardTitle>
                <Badge variant="outline" className="uppercase">
                  {s.transport}
                </Badge>
                <div className="ml-auto flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                  <Switch
                    checked={s.enabled}
                    disabled={busyIds.has(s.id)}
                    onCheckedChange={() => toggleEnabled(s)}
                    aria-label="Enable"
                  />
                  <Button
                    size="icon"
                    variant="outline"
                    aria-label="Delete"
                    disabled={busyIds.has(s.id)}
                    onClick={() => removeServer(s)}
                  >
                    <Trash2Icon className="text-destructive" />
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="grid gap-3">
              <p className="text-muted-foreground text-sm">
                {s.tools && s.tools.length > 0 ? `${s.tools.length} tools` : "No tools discovered"}
              </p>
              <div className="grid gap-2" onClick={(e) => e.stopPropagation()}>
                <span className="text-muted-foreground text-xs">Visibility (per-agent access)</span>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {agents.map((a) => (
                    <label key={a.key} className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={(visibility[s.id] ?? []).includes(a.id)}
                        disabled={busyIds.has(s.id)}
                        onCheckedChange={() => toggleVisibility(s.id, a.id, a.name)}
                      />
                      {a.name}
                    </label>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side="right"
          className="w-full data-[side=right]:sm:max-w-lg"
        >
          <SheetHeader>
            <SheetTitle>{editing ? editing.name : "Add MCP server"}</SheetTitle>
            <SheetDescription>
              stdio (local process) or http (remote Streamable HTTP)
            </SheetDescription>
          </SheetHeader>

          {editing ? (
            <Tabs
              value={tab}
              onValueChange={(v) => setTab(v as "config" | "tools")}
              className="flex min-h-0 flex-1 flex-col px-4"
            >
              <TabsList>
                <TabsTrigger value="config">Configuration</TabsTrigger>
                <TabsTrigger value="tools">
                  Tool list{tools.length ? `（${tools.length}）` : ""}
                </TabsTrigger>
              </TabsList>
              <TabsContent value="config" className="min-h-0 flex-1 overflow-y-auto">
                {renderForm()}
                <div className="flex gap-2 pt-2 pb-6">
                  <Button onClick={saveForm} disabled={saving || busyIds.has(editing.id)}>
                    Save
                  </Button>
                </div>
              </TabsContent>
              <TabsContent value="tools" className="min-h-0 flex-1 overflow-y-auto">
                {renderTools()}
              </TabsContent>
            </Tabs>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4">
              {renderForm()}
              <div className="pt-2 pb-6">
                <Button onClick={saveForm} disabled={saving}>
                  <PlusIcon /> Add
                </Button>
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
