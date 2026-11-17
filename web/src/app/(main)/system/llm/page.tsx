"use client";

import * as React from "react";

import {
  Loader2Icon,
  PlugZapIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SaveIcon,
  StarIcon,
  Trash2Icon,
  ZapIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { LLMPoolMember, LLMPoolStatus, LLMProfile } from "@/lib/types";
import { cn } from "@/lib/utils";

import { ProfileRetryFields, RetryPolicyPanel, ZERO_OVERRIDE } from "./_components/retry";

// The thinking.type switch and reasoning_effort are independent fields.
// Configure them separately: some APIs enable reasoning through effort alone, without thinking.
// An empty stored string means omit the field; Radix Select requires the "none"
// sentinel, mapped to/from an empty string by NONE / fromStore / toStore.
const NONE = "none";
const fromStore = (v?: string) => (v ? v : NONE);
const toStore = (v: string) => (v === NONE ? "" : v);
const THINKING_TYPES: { value: string; label: string }[] = [
  { value: NONE, label: "Omit (default)" },
  { value: "disabled", label: "Disabled" },
  { value: "enabled", label: "Enabled" },
];
// Output-limit request field (openai format only). NONE and empty string use the same sentinel conversion.
const MAX_TOKENS_FIELDS: { value: string; label: string }[] = [
  { value: NONE, label: "max_tokens (default)" },
  { value: "max_completion_tokens", label: "max_completion_tokens" },
];
// Other formats have fixed field names, as explained in their help text.
const MAX_TOKENS_FIELD_HINTS: Record<string, string> = {
  openai:
    "Select the output-limit key. max_tokens is the default accepted by most compatible gateways; official OpenAI reasoning models (o-series / GPT-5) require max_completion_tokens and reject max_tokens with unsupported_parameter.",
  anthropic: "Selectable only for openai format. Anthropic always uses max_tokens.",
  "openai-responses": "Selectable only for openai format. Responses API always uses max_output_tokens.",
};
const EFFORT_LEVELS: { value: string; label: string }[] = [
  { value: NONE, label: "Omit (default)" },
  { value: "low", label: "low" },
  { value: "medium", label: "medium" },
  { value: "high", label: "high" },
  { value: "xhigh", label: "xhigh" },
  { value: "max", label: "max" },
];

function cooldownText(secs: number) {
  if (secs <= 0) return "";
  if (secs < 60) return `${secs}s`;
  return `${Math.ceil(secs / 60)}min`;
}

// Profile health on its card. A missing key prevents requests and takes precedence over breaker state.
// Other states come from failover breaker records; when failover is disabled, healthy means no known fault.
type Health = { label: string; cls: string; hint?: string };
function healthOf(p: LLMProfile, m?: LLMPoolMember): Health {
  if (!p.api_key_hint) {
    return {
      label: "Missing key",
      cls: "border-muted-foreground/40 text-muted-foreground",
      hint: "No API key configured; requests cannot be sent",
    };
  }
  if (m?.state === "tripped") {
    return {
      label: m.cooldown_secs > 0 ? `Circuit open · ${cooldownText(m.cooldown_secs)}` : "Circuit open",
      cls: "border-destructive/50 text-destructive",
      hint: m.last_error,
    };
  }
  if (m?.state === "degraded") {
    return {
      label: `Unhealthy · failures: ${m.fails} times`,
      cls: "border-amber-500/50 text-amber-600 dark:text-amber-400",
      hint: m.last_error,
    };
  }
  return { label: "Healthy", cls: "border-emerald-500/50 text-emerald-600 dark:text-emerald-400" };
}

// ─────────────────────────────────────────────────────────────────────────────
// Failover configuration drawer
// ─────────────────────────────────────────────────────────────────────────────

function PoolSheet({
  open,
  onOpenChange,
  pool,
  onReload,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  pool: LLMPoolStatus | null;
  onReload: () => Promise<void>;
}) {
  const [busy, setBusy] = React.useState(false);

  // The server computes cooldown seconds; poll while the drawer is open and any profile is unhealthy.
  React.useEffect(() => {
    if (!open || !pool?.enabled || !pool.chain.some((m) => m.state !== "ok")) return;
    const t = setInterval(() => void onReload(), 10_000);
    return () => clearInterval(t);
  }, [open, pool, onReload]);

  async function toggle(patch: { llm_pool_enabled?: boolean; llm_pool_bind_fallback?: boolean }) {
    if (busy) return;
    setBusy(true);
    try {
      await api.setSettings(patch);
      await onReload();
      if (patch.llm_pool_enabled !== undefined) {
        toast.success(patch.llm_pool_enabled ? "LLM failover enabled" : "LLM failover disabled");
      } else {
        toast.success("Fallback settings updated");
      }
    } catch (e) {
      toast.error(`Settings failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function recover(id?: string) {
    try {
      await api.resetLLMPool(id);
      await onReload();
      toast.success(id ? "Profile restored" : "All profiles restored");
    } catch (e) {
      toast.error(`Restore failed: ${(e as Error).message}`);
    }
  }

  const enabled = pool?.enabled ?? false;
  const chain = pool?.chain ?? [];
  // Failover members, excluding opted-out profiles, in the actual server attempt order.
  const inChain = chain.filter((m) => m.active || !m.excluded);
  const tripped = chain.filter((m) => m.state === "tripped");

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex flex-col gap-0 p-0 data-[side=right]:sm:max-w-lg">
        <SheetHeader className="px-4">
          <SheetTitle className="flex items-center gap-2">
            <ZapIcon className="size-4" /> LLM failover
          </SheetTitle>
          <SheetDescription>
            When enabled, agents <b>without an assigned model</b> switch to the next profile when the current one is unavailable (insufficient credit / invalid key / rate limiting /
            service failure).
          </SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-6">
          <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
            <div className="grid gap-0.5">
              <Label className="text-sm">Enable failover</Label>
              <p className="text-muted-foreground text-xs">Disabled by default. When disabled, only the active profile is used; failures are not retried with another profile.</p>
            </div>
            <Switch
              checked={enabled}
              disabled={busy}
              onCheckedChange={(v) => void toggle({ llm_pool_enabled: v })}
              aria-label="LLM failover switch"
            />
          </div>

          {enabled && (
            <>
              <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
                <div className="grid gap-0.5">
                  <Label className="text-sm">Allow fallback for explicitly assigned models</Label>
                  <p className="text-muted-foreground text-xs">
                    Disabled by default: an agent or task assigned a profile uses only that profile, without silently switching models on failure.
                    When enabled, failures of assigned profiles also fall back to the chain below.
                  </p>
                </div>
                <Switch
                  checked={pool?.bind_fallback ?? false}
                  disabled={busy}
                  onCheckedChange={(v) => void toggle({ llm_pool_bind_fallback: v })}
                  aria-label="Assigned-profile fallback switch"
                />
              </div>

              <Separator />

              <div className="grid gap-2">
                <div className="flex items-center justify-between">
                  <Label className="text-sm">Failover order</Label>
                  {tripped.length > 0 && (
                    <Button size="sm" variant="ghost" onClick={() => void recover()}>
                      <RotateCcwIcon /> Restore all
                    </Button>
                  )}
                </div>
                {inChain.length < 2 && (
                  <p className="text-muted-foreground text-xs">
                    Only {inChain.length} usable profiles are available. Failover requires at least two participating profiles with API keys.
                  </p>
                )}
                {chain.map((m) => {
                  const excluded = m.excluded && !m.active;
                  const order = excluded ? null : inChain.findIndex((x) => x.profile_id === m.profile_id) + 1;
                  return (
                    <div
                      key={m.profile_id}
                      className={cn(
                        "grid gap-1 rounded-lg border p-2.5 text-sm",
                        excluded && "opacity-55",
                        m.state === "tripped" && "border-destructive/40",
                      )}
                    >
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="w-5 shrink-0 text-center font-mono text-muted-foreground text-xs">
                          {order ?? "—"}
                        </span>
                        <span className="font-medium">{m.name}</span>
                        {m.active && (
                          <Badge variant="outline" className="border-amber-400/50 text-amber-500">
                            Active
                          </Badge>
                        )}
                        {excluded && <Badge variant="outline">Exclude from failover</Badge>}
                        <div className="ml-auto flex items-center gap-2">
                          {m.state === "tripped" && m.cooldown_secs > 0 && (
                            <span className="text-muted-foreground text-xs">Cooldown {cooldownText(m.cooldown_secs)}</span>
                          )}
                          {m.state === "degraded" && (
                            <span className="text-muted-foreground text-xs">Consecutive failures: {m.fails}</span>
                          )}
                          {m.state !== "ok" && (
                            <Button
                              size="icon"
                              variant="ghost"
                              className="size-7"
                              aria-label="Restore now"
                              title="Restore now: clear the breaker and retry this profile on the next call"
                              onClick={() => void recover(m.profile_id)}
                            >
                              <RotateCcwIcon className="size-3.5" />
                            </Button>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 pl-7 text-muted-foreground text-xs">
                        <code className="truncate font-mono">{m.model}</code>
                        {!m.active && <span>Priority {m.priority}</span>}
                      </div>
                      {m.last_error && (
                        <p className="truncate pl-7 font-mono text-muted-foreground text-xs" title={m.last_error}>
                          {m.last_error}
                        </p>
                      )}
                    </div>
                  );
                })}
                {chain.length === 0 && (
                  <div className="rounded-lg border border-dashed p-4 text-center text-muted-foreground text-sm">
                    No profiles
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-dashed p-3 text-muted-foreground text-xs leading-relaxed">
                The active profile always comes first; others follow descending priority (configured per profile). Failed profiles enter cooldown (60s → 5min →
                30min), are skipped during cooldown, and become eligible again after recovery. Profiles whose context windows cannot fit the request are skipped. Agents
                and tasks with assigned models do not use failover by default.
              </div>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Model profile drawer (shared create/edit form)
// ─────────────────────────────────────────────────────────────────────────────

function initialProfileDraft(profile: LLMProfile | null) {
  return {
    name: profile?.name ?? "",
    format: (profile?.format === "openai" || profile?.format === "openai-responses"
      ? profile.format
      : "anthropic") as LLMProfile["format"],
    model: profile?.model ?? "",
    baseUrl: profile?.base_url ?? "",
    proxy: profile?.proxy ?? "",
    apiKey: "",
    rps: String(profile?.rate_per_second ?? 0),
    rpm: String(profile?.rate_per_minute ?? 0),
    cw: String(profile?.context_window_k ?? 0),
    thinkingType: fromStore(profile?.thinking_type),
    effort: fromStore(profile?.reasoning_effort),
    priority: String(profile?.priority ?? 0),
    poolExclude: profile?.pool_exclude ?? false,
    streaming: profile?.streaming ?? true,
    maxTokens: String(profile?.max_tokens ?? 0),
    maxTokensField: fromStore(profile?.max_tokens_field),
    sessionHeaderKey: profile?.session_header_key ?? "",
    retry: structuredClone(profile?.retry ?? ZERO_OVERRIDE),
  };
}

// The transport's TypeScript return type is not a runtime receipt check. Never
// turn an update into a create (or adopt another profile) from malformed JSON.
function acknowledgedProfileId(receipt: unknown, submittedId: number | undefined): number | null {
  if (!receipt || typeof receipt !== "object" || Array.isArray(receipt)) return null;
  const id = (receipt as { id?: unknown }).id;
  if (typeof id !== "number" || !Number.isSafeInteger(id) || id <= 0) return null;
  if (submittedId !== undefined && id !== submittedId) return null;
  return id;
}

function ProfileSheet({
  profile,
  open,
  isCurrent,
  onOpenChange,
  onSaved,
}: {
  profile: LLMProfile | null; // null = create
  open: boolean;
  isCurrent: () => boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: (id: string) => void;
}) {
  // The parent keys each open/switch as a separate session. Hydrate once: a
  // background catalog refresh must never replace an unsaved draft.
  const [draft, setDraft] = React.useState(() => initialProfileDraft(profile));
  const draftRef = React.useRef(draft);
  const {
    name,
    format,
    model,
    baseUrl,
    proxy,
    apiKey,
    rps,
    rpm,
    cw,
    thinkingType,
    effort,
    priority,
    poolExclude,
    streaming,
    maxTokens,
    maxTokensField,
    sessionHeaderKey,
    retry,
  } = draft;
  const keyHint = profile?.api_key_hint ?? "";
  const [storedId, setStoredId] = React.useState(profile ? Number(profile.id) : undefined);
  const profileId = React.useRef(storedId);
  const isNew = storedId === undefined;
  const [testing, setTesting] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [saveUnverified, setSaveUnverified] = React.useState(false);
  const saveUnverifiedRef = React.useRef(false);
  const [models, setModels] = React.useState<string[]>([]);
  const [loadingModels, setLoadingModels] = React.useState(false);
  const [modelsOpen, setModelsOpen] = React.useState(false);
  type Operation = "models" | "test" | "save";
  type Owner = { draft: typeof draft };
  const requests = React.useRef<Record<Operation, Owner | null>>({ models: null, test: null, save: null });
  const alive = React.useRef(false);
  React.useLayoutEffect(() => {
    alive.current = true;
    setLoadingModels(requests.current.models !== null);
    setTesting(requests.current.test !== null);
    setSaving(requests.current.save !== null);
    return () => {
      alive.current = false;
    };
  }, []);

  function editDraft(patch: Partial<typeof draft>) {
    if (!alive.current || !isCurrent()) return;
    const next = { ...draftRef.current, ...structuredClone(patch) };
    draftRef.current = next;
    setDraft(next);
    if ("format" in patch || "baseUrl" in patch || "proxy" in patch || "apiKey" in patch) {
      // A different endpoint/key can start a new list request immediately. The
      // retired request may finish, but cannot publish or clear that new owner.
      requests.current.models = null;
      setLoadingModels(false);
      setModels([]);
      setModelsOpen(false);
    }
  }
  function begin(kind: Operation): Owner | null {
    if (!alive.current || !isCurrent() || saveUnverifiedRef.current || requests.current[kind]) return null;
    const owner = { draft: draftRef.current };
    requests.current[kind] = owner;
    return owner;
  }
  function owns(kind: Operation, owner: Owner) {
    return alive.current && isCurrent() && requests.current[kind] === owner;
  }
  function finish(kind: Operation, owner: Owner, setBusy: (busy: boolean) => void) {
    if (requests.current[kind] !== owner) return;
    requests.current[kind] = null;
    if (alive.current && isCurrent()) setBusy(false);
  }

  function retireProbes() {
    // A save can change stored-key fallback even if its profile ID is unchanged.
    // Completed choices and pending probes belong to the pre-save context.
    requests.current.models = null;
    requests.current.test = null;
    setLoadingModels(false);
    setTesting(false);
    setModels([]);
    setModelsOpen(false);
  }

  async function loadModels() {
    const owner = begin("models");
    if (!owner) return;
    const submitted = owner.draft;
    setLoadingModels(true);
    setModels([]);
    setModelsOpen(false);
    try {
      const r = await api.fetchLLMModels(
        submitted.format,
        submitted.baseUrl,
        submitted.apiKey,
        submitted.proxy,
        profileId.current,
      );
      if (!owns("models", owner)) return;
      if (r.ok && r.models && r.models.length > 0) {
        setModels(r.models);
        setModelsOpen(true);
        toast.success(`Loaded ${r.models.length} models`);
      } else {
        toast.error(`Failed to load models: ${r.error ?? "No models returned"}`);
      }
    } catch (e) {
      if (owns("models", owner)) toast.error(`Model loading error: ${(e as Error).message}`);
    } finally {
      finish("models", owner, setLoadingModels);
    }
  }

  async function testConnection() {
    const owner = begin("test");
    if (!owner) return;
    const submitted = owner.draft;
    setTesting(true);
    try {
      // Empty key uses the stored profile key; reasoning/streaming/header values
      // are the exact submitted draft, even if the form changes while waiting.
      const r = await api.testLLM(
        submitted.format,
        submitted.model,
        submitted.baseUrl,
        submitted.apiKey,
        submitted.proxy,
        toStore(submitted.thinkingType),
        toStore(submitted.effort),
        profileId.current,
        submitted.streaming,
        submitted.sessionHeaderKey.trim(),
      );
      if (!owns("test", owner) || draftRef.current !== submitted) return;
      if (r.ok)
        toast.success(`Connected · ${r.latency_ms ?? "?"}ms · ${r.model ?? submitted.model}`, {
          description: r.reply ? `Reply: ${r.reply}` : undefined,
        });
      else toast.error(`Connection failed: ${r.error ?? "Unknown"}`);
    } catch (e) {
      if (owns("test", owner) && draftRef.current === submitted) toast.error(`Test error: ${(e as Error).message}`);
    } finally {
      finish("test", owner, setTesting);
    }
  }

  async function save() {
    if (!alive.current || !isCurrent() || saveUnverifiedRef.current || requests.current.save) return;
    const submitted = draftRef.current;
    if (!submitted.name.trim() || !submitted.model.trim()) {
      toast.error("Enter a name and model");
      return;
    }
    const owner = begin("save");
    if (!owner) return;
    const submittedId = profileId.current;
    const creating = submittedId === undefined;
    setSaving(true);
    try {
      const receipt = await api.saveLLMProfile({
        ...(creating ? {} : { id: submittedId }),
        name: submitted.name.trim(),
        format: submitted.format,
        model: submitted.model.trim(),
        base_url: submitted.baseUrl.trim(),
        proxy: submitted.proxy.trim(),
        api_key: submitted.apiKey,
        rate_per_second: Number(submitted.rps) || 0,
        rate_per_minute: Number(submitted.rpm) || 0,
        context_window_k: Number(submitted.cw) || 0,
        thinking_type: toStore(submitted.thinkingType),
        reasoning_effort: toStore(submitted.effort),
        priority: Number(submitted.priority) || 0,
        pool_exclude: submitted.poolExclude,
        streaming: submitted.streaming,
        max_tokens: Math.max(0, Number(submitted.maxTokens) || 0),
        // Only Chat Completions accepts an alternative output-limit field.
        max_tokens_field: submitted.format === "openai" ? toStore(submitted.maxTokensField) : "",
        session_header_key: submitted.sessionHeaderKey.trim(),
        retry: submitted.retry,
      });
      if (!owns("save", owner)) return;
      retireProbes();
      const id = acknowledgedProfileId(receipt, submittedId);
      if (id === null) {
        // The server may have committed despite an invalid receipt. Keep the
        // draft/target, but do not blindly create again or certify its key.
        saveUnverifiedRef.current = true;
        setSaveUnverified(true);
        toast.error("Save outcome could not be verified. Reload this page before saving again.");
        return;
      }
      // If a create succeeds while the user keeps editing, the next Save must
      // update this acknowledged profile instead of creating a duplicate.
      profileId.current = id;
      setStoredId(id);
      const changed = draftRef.current !== submitted;
      if (changed) toast.success("Saved submitted values; newer edits remain unsaved");
      else if (creating) toast.success(`Created: ${submitted.name.trim()} (select Set active on the card to enable)`);
      else
        toast.success(
          profile?.is_default ? "Saved; active-profile changes apply immediately without restart" : "Saved",
        );
      onSaved(String(id));
      if (!changed && owns("save", owner)) onOpenChange(false);
    } catch (e) {
      if (owns("save", owner)) toast.error(`Save failed: ${(e as Error).message}`);
    } finally {
      finish("save", owner, setSaving);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex flex-col gap-0 p-0 data-[side=right]:min-w-[420px] data-[side=right]:sm:max-w-xl"
      >
        <SheetHeader className="px-4">
          <SheetTitle className="flex items-center gap-2">
            {isNew ? "New model profile" : `Edit: ${profile?.name ?? name}`}
            {profile?.is_default && (
              <Badge variant="outline" className="border-amber-400/50 text-amber-500">
                Active
              </Badge>
            )}
          </SheetTitle>
          <SheetDescription>
            {isNew
              ? "New profiles are not activated automatically. Select Set active on the card to enable them."
              : "Save your changes; changes to the active profile apply to all agents immediately."}
          </SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="p-name">Name</Label>
              <Input
                id="p-name"
                placeholder="Example: OpenAI production"
                value={name}
                onChange={(e) => editDraft({ name: e.target.value })}
              />
            </div>
            <div className="grid gap-2">
              <Label>Format</Label>
              <Select value={format} onValueChange={(v) => editDraft({ format: v as LLMProfile["format"] })}>
                <SelectTrigger>
                  <SelectValue placeholder="Select format" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="anthropic">Anthropic</SelectItem>
                  <SelectItem value="openai">OpenAI (Chat Completions)</SelectItem>
                  <SelectItem value="openai-responses">OpenAI (Responses API)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="p-model">Model</Label>
            <div className="flex gap-2">
              <Input
                id="p-model"
                className="font-mono"
                placeholder="claude-opus-4-8"
                value={model}
                onChange={(e) => editDraft({ model: e.target.value })}
              />
              {/* modal: this Popover is portaled to <body>, outside the Sheet scroll lock.
                  Without modal, the list renders but cannot scroll. Modal gives it the top scroll lock. */}
              <Popover open={modelsOpen} onOpenChange={setModelsOpen} modal>
                <PopoverTrigger asChild>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="shrink-0"
                    disabled={loadingModels || saveUnverified}
                    onClick={loadModels}
                    title="Load available models from API"
                  >
                    {loadingModels ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
                  </Button>
                </PopoverTrigger>
                {models.length > 0 && (
                  <PopoverContent className="max-h-72 w-72 gap-0 overflow-y-auto overscroll-contain p-1" align="end">
                    {models.map((m) => (
                      <button
                        key={m}
                        type="button"
                        className="w-full shrink-0 rounded-md px-2 py-1.5 text-left font-mono text-xs hover:bg-accent hover:text-accent-foreground"
                        onClick={() => {
                          editDraft({ model: m });
                          setModelsOpen(false);
                        }}
                      >
                        {m}
                      </button>
                    ))}
                  </PopoverContent>
                )}
              </Popover>
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="p-base-url">Base URL (optional)</Label>
            <Input
              id="p-base-url"
              className="font-mono"
              placeholder="https://api.openai.com/v1"
              value={baseUrl}
              onChange={(e) => editDraft({ baseUrl: e.target.value })}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="p-proxy">Proxy (optional)</Label>
            <Input
              id="p-proxy"
              className="font-mono"
              placeholder="socks5://user:pass@127.0.0.1:1080 · http://127.0.0.1:8080"
              value={proxy}
              onChange={(e) => editDraft({ proxy: e.target.value })}
            />
            <p className="text-muted-foreground text-xs">
              Only outbound LLM requests use this proxy. Supports http/https/socks5 and credentials (for example,
              socks5://user:pass@host:port; URL-encode special password characters). Leave blank for a direct connection.
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="p-session-header">Custom session header (optional)</Label>
            <Input
              id="p-session-header"
              className="font-mono"
              placeholder="For example, x-session-id (blank = omit)"
              value={sessionHeaderKey}
              onChange={(e) => editDraft({ sessionHeaderKey: e.target.value })}
            />
            <p className="text-muted-foreground text-xs">
              Every request includes this HTTP header with the <b>current session ID</b> (chat sessions such as
              conv-12, workers such as exp3-worker-i87). Useful for gateways that use the session-id header for prompt caching /
              sticky routing. Stable across turns within a session and distinct across sessions. Leave blank to omit.
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="p-api-key">API Key</Label>
            <Input
              id="p-api-key"
              type="password"
              placeholder={keyHint ? `Configured (${keyHint}); leave blank to keep unchanged` : "sk-…"}
              value={apiKey}
              onChange={(e) => editDraft({ apiKey: e.target.value })}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="grid gap-2">
              <Label htmlFor="p-rps">Requests per second</Label>
              <Input id="p-rps" type="number" min={0} value={rps} onChange={(e) => editDraft({ rps: e.target.value })} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="p-rpm">Requests per minute</Label>
              <Input id="p-rpm" type="number" min={0} value={rpm} onChange={(e) => editDraft({ rpm: e.target.value })} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="p-cw">Context window (K)</Label>
              <Input
                id="p-cw"
                type="number"
                min={0}
                max={1000}
                value={cw}
                onChange={(e) => editDraft({ cw: e.target.value })}
                placeholder="200"
              />
            </div>
          </div>
          <p className="-mt-2 text-muted-foreground text-xs">
            Rate limit 0 = unlimited, shared by all agents. Context window is in K (thousand tokens); 0 = default 200K, maximum 1000 (
            1M). Setting it too high can prevent compaction from triggering.
          </p>

          <div className="grid gap-3 rounded-lg border p-3">
            <div className="flex items-center justify-between gap-4">
              <div className="grid gap-0.5">
                <Label htmlFor="p-priority" className="text-sm">
                  Failover priority
                </Label>
                <p className="text-muted-foreground text-xs">
                  Higher values are selected first; the active profile always comes first regardless. Equal-priority profiles rotate to distribute usage.
                </p>
              </div>
              <Input
                id="p-priority"
                type="number"
                className="w-24 shrink-0"
                value={priority}
                onChange={(e) => editDraft({ priority: e.target.value })}
              />
            </div>
            <div className="flex items-center justify-between gap-4 border-t pt-3">
              <div className="grid gap-0.5">
                <Label className="text-sm">Exclude from failover</Label>
                <p className="text-muted-foreground text-xs">
                  When enabled, this profile is excluded as a failover target (agents/tasks may still assign it explicitly). Useful for expensive profiles reserved for a particular agent
                  that should not consume quota when other profiles fail.
                </p>
              </div>
              <Switch checked={poolExclude} onCheckedChange={(value) => editDraft({ poolExclude: value })} aria-label="Exclude from failover" />
            </div>
            <div className="flex items-center justify-between gap-4 border-t pt-3">
              <div className="grid gap-0.5">
                <Label className="text-sm">Streaming output</Label>
                <p className="text-muted-foreground text-xs">
                  Enabled by default: SSE streaming provides live progress and token counts. Disabled uses true non-streaming (stream:false,
                  returning the complete response at once). This can avoid faulty gateway SSE implementations (empty frames / missing reasoning frames),
                  at the cost of live progress.
                </p>
              </div>
              <Switch checked={streaming} onCheckedChange={(value) => editDraft({ streaming: value })} aria-label="Streaming output" />
            </div>
          </div>

          <div className="grid gap-3 rounded-lg border p-3">
            <div className="flex items-center justify-between gap-4">
              <div className="grid gap-0.5">
                <Label htmlFor="p-max-tokens" className="text-sm">
                  Output limit · max tokens
                </Label>
                <p className="text-muted-foreground text-xs">
                  Maximum generated tokens per response, sent with each request. 0 (default) omits the field and uses the server default.
                  This differs from the context window above, which is the total model capacity used locally to compute compaction thresholds.
                  Too small a limit may truncate reasoning before the model produces any answer.
                </p>
              </div>
              <Input
                id="p-max-tokens"
                type="number"
                min={0}
                className="w-28 shrink-0"
                value={maxTokens}
                onChange={(e) => editDraft({ maxTokens: e.target.value })}
                placeholder="0"
              />
            </div>
            <div className="flex items-center justify-between gap-4 border-t pt-3">
              <div className="grid gap-0.5">
                <Label className="text-sm">Output-limit field</Label>
                <p className="text-muted-foreground text-xs">{MAX_TOKENS_FIELD_HINTS[format]}</p>
              </div>
              <Select
                value={format === "openai" ? maxTokensField : NONE}
                onValueChange={(value) => editDraft({ maxTokensField: value })}
                disabled={format !== "openai"}
              >
                <SelectTrigger className="w-56 shrink-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MAX_TOKENS_FIELDS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid gap-3 rounded-lg border p-3">
            <div className="flex items-center justify-between gap-4">
              <div className="grid gap-0.5">
                <Label className="text-sm">Thinking switch · thinking.type</Label>
                <p className="text-muted-foreground text-xs">
                  Controls the thinking field. Omit excludes it (for models such as MiniMax that do not support it); disabled sends
                  disabled; enabled sends enabled. This is independent of the effort below.
                </p>
              </div>
              <Select value={thinkingType} onValueChange={(value) => editDraft({ thinkingType: value })}>
                <SelectTrigger className="w-32 shrink-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {THINKING_TYPES.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between gap-4 border-t pt-3">
              <div className="grid gap-0.5">
                <Label className="text-sm">Reasoning effort · reasoning_effort</Label>
                <p className="text-muted-foreground text-xs">
                  Independent effort level (OpenAI reasoning_effort / Anthropic output_config.effort). Some APIs have no thinking
                  field and enable reasoning through effort alone; configure it independently without sending the thinking switch.
                </p>
              </div>
              <Select value={effort} onValueChange={(value) => editDraft({ effort: value })}>
                <SelectTrigger className="w-32 shrink-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EFFORT_LEVELS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <ProfileRetryFields value={retry} onChange={(value) => editDraft({ retry: value })} />
        </div>

        {saveUnverified && (
          <p role="alert" className="px-4 py-2 text-sm text-destructive">
            Save outcome could not be verified. Copy any unsaved edits, then reload this page before saving again.
          </p>
        )}
        <div className="flex gap-2 border-t px-4 py-3">
          <Button variant="outline" onClick={testConnection} disabled={testing || saveUnverified}>
            {testing ? <Loader2Icon className="animate-spin" /> : <PlugZapIcon />}
            {testing ? "Testing…" : "Test connection"}
          </Button>
          <Button onClick={save} disabled={saving || saveUnverified} className="flex-1">
            {saving && <Loader2Icon className="animate-spin" />}
            {!saving && (isNew ? <PlusIcon /> : <SaveIcon />)}
            {isNew ? "New" : "Save"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

export default function LLMPage() {
  const [profiles, setProfiles] = React.useState<LLMProfile[]>([]);
  const [pool, setPool] = React.useState<LLMPoolStatus | null>(null);
  const [poolOpen, setPoolOpen] = React.useState(false);
  // Preserve content during close animation; every open (including the same
  // profile) gets a fresh keyed draft. The ref retires callbacks synchronously,
  // before React commits a queued close/switch or unmounts the previous editor.
  type Editor = { profile: LLMProfile | null; open: boolean; session: number };
  const [editor, setEditor] = React.useState<Editor>({ profile: null, open: false, session: 0 });
  const editorRef = React.useRef(editor);
  const openEditor = React.useCallback((profile: LLMProfile | null) => {
    const next = { profile: structuredClone(profile), open: true, session: editorRef.current.session + 1 };
    editorRef.current = next;
    setEditor(next);
  }, []);
  const changeEditorOpen = React.useCallback((session: number, open: boolean) => {
    if (editorRef.current.session !== session) return;
    const next = { ...editorRef.current, open };
    editorRef.current = next;
    setEditor(next);
  }, []);

  const loadPool = React.useCallback(async () => {
    try {
      setPool(await api.llmPool());
    } catch {
      /* ignore */
    }
  }, []);

  const load = React.useCallback(async () => {
    try {
      setProfiles(await api.llmProfiles());
    } catch {
      /* ignore */
    }
    await loadPool();
  }, [loadPool]);

  React.useEffect(() => {
    void load();
  }, [load]);

  // Card health badges use failover status by profile ID.
  const health = React.useMemo(() => {
    const m = new Map<string, LLMPoolMember>();
    for (const c of pool?.chain ?? []) m.set(c.profile_id, c);
    return m;
  }, [pool]);

  async function activate(id: string, name: string) {
    try {
      await api.activateLLMProfile(id);
      toast.success(`Activated: ${name}`);
      await load();
    } catch (e) {
      toast.error(`Activation failed: ${(e as Error).message}`);
    }
  }

  async function remove(p: LLMProfile) {
    if (p.is_default) {
      toast.error("Cannot delete the active profile");
      return;
    }
    try {
      await api.deleteLLMProfile(p.id);
      toast.success(`Deleted: ${p.name}`);
      await load();
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
    }
  }

  const poolOn = pool?.enabled ?? false;

  return (
    <div className="flex flex-1 flex-col gap-4 md:gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-semibold text-xl tracking-tight">LLM</h1>
          <p className="text-muted-foreground text-sm">
            Format, model, and rate-limit settings shared by all agents. Click a card to edit; the star marks the active profile.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setPoolOpen(true)}>
            <ZapIcon /> Failover settings
            {poolOn && (
              <Badge variant="outline" className="ml-1 border-emerald-500/50 text-emerald-600 dark:text-emerald-400">
                Enabled
              </Badge>
            )}
          </Button>
          <Button size="sm" variant="outline" onClick={() => openEditor(null)}>
            <PlusIcon /> New
          </Button>
        </div>
      </div>

      <Tabs defaultValue="profiles" className="flex-1">
        <TabsList>
          <TabsTrigger value="profiles">Model profiles</TabsTrigger>
          <TabsTrigger value="retry">Retries and backoff</TabsTrigger>
        </TabsList>

        <TabsContent value="profiles" className="mt-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {profiles.map((p) => {
              const h = healthOf(p, health.get(p.id));
              return (
                // biome-ignore lint/a11y/useSemanticElements: The card contains action buttons; a native button would create invalid nested buttons.
                <Card
                  key={p.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => openEditor(p)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      openEditor(p);
                    }
                  }}
                  className={cn(
                    "cursor-pointer gap-0 py-4 outline-none transition-colors hover:border-foreground/30",
                    p.is_default && "border-amber-400/50 bg-amber-400/5",
                  )}
                >
                  <CardContent className="grid gap-2 px-4">
                    <div className="flex items-start gap-2">
                      <StarIcon
                        className={cn(
                          "mt-0.5 size-4 shrink-0",
                          p.is_default ? "fill-amber-400 text-amber-400" : "text-muted-foreground",
                        )}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate font-medium text-sm">{p.name}</span>
                          <Badge variant="outline" className="uppercase">
                            {p.format}
                          </Badge>
                          <Badge variant="outline" className={cn("ml-auto", h.cls)} title={h.hint}>
                            {h.label}
                          </Badge>
                        </div>
                        <code className="mt-1 block truncate font-mono text-muted-foreground text-xs">{p.model}</code>
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 pl-6 text-muted-foreground text-xs">
                      {p.api_key_hint && <span>{p.api_key_hint}</span>}
                      <span>
                        {p.rate_per_second}/s · {p.rate_per_minute}/min
                      </span>
                      {p.proxy && <span className="truncate">Proxy {p.proxy}</span>}
                      {p.reasoning_effort && (
                        <span>Reasoning {p.reasoning_effort === "off" ? "off" : p.reasoning_effort}</span>
                      )}
                      {/* The two failover fields are shown only when failover is enabled. */}
                      {poolOn &&
                        !p.is_default &&
                        (p.pool_exclude ? <span>Exclude from failover</span> : <span>Priority {p.priority ?? 0}</span>)}
                    </div>

                    <div className="mt-1 flex gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1"
                        disabled={p.is_default}
                        onClick={(e) => {
                          e.stopPropagation();
                          void activate(p.id, p.name);
                        }}
                      >
                        {p.is_default ? "Active" : "Set active"}
                      </Button>
                      <Button
                        size="icon"
                        variant="outline"
                        aria-label="Delete profile"
                        onClick={(e) => {
                          e.stopPropagation();
                          void remove(p);
                        }}
                      >
                        <Trash2Icon className="text-destructive" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
            {profiles.length === 0 && (
              <div className="col-span-full rounded-lg border border-dashed p-10 text-center text-muted-foreground text-sm">
                No model profiles yet. Click New in the top-right to create one.
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="retry" className="mt-4">
          <RetryPolicyPanel />
        </TabsContent>
      </Tabs>

      <ProfileSheet
        key={editor.session}
        profile={editor.profile}
        open={editor.open}
        isCurrent={() => editorRef.current.open && editorRef.current.session === editor.session}
        onOpenChange={(open) => changeEditorOpen(editor.session, open)}
        onSaved={() => void load()}
      />
      <PoolSheet open={poolOpen} onOpenChange={setPoolOpen} pool={pool} onReload={loadPool} />
    </div>
  );
}
