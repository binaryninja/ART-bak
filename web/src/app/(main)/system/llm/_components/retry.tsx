"use client";

// Shared LLM retry configuration: attempt counts and intervals for five retry layers.
//
// From inner to outer: connection (SDK), empty response (SDK), same-provider safe window, failover circuit breaker, intent rerun.
// The first three follow the endpoint and allow per-profile overrides; the last two are process-wide global policies.
//
// All inputs use empty = unconfigured semantics, matching backend db.RetryRule:
//   Attempts: empty/0 = built-in default | -1 = disable this layer | positive = specified count
//   Interval: empty/0 = original exponential backoff | positive = fixed interval in milliseconds

import * as React from "react";

import { Loader2Icon, SaveIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { LLMRetryOverride, LLMRetryPolicy, LLMRetryRule } from "@/lib/types";

export const ZERO_RULE: LLMRetryRule = { attempts: 0, interval_ms: 0 };
export const ZERO_OVERRIDE: LLMRetryOverride = {
  connect: ZERO_RULE,
  empty: ZERO_RULE,
  stream: ZERO_RULE,
};
const ZERO_POLICY: LLMRetryPolicy = {
  ...ZERO_OVERRIDE,
  breaker: ZERO_RULE,
  intent: ZERO_RULE,
};

type LayerMeta = {
  title: string;
  /** Where this retry layer runs and who executes it */
  where: string;
  /** Errors handled by this layer, with explicit status codes */
  trigger: string;
  /** Similar errors not handled by this layer, to avoid confusing configuration with bugs */
  skips?: string;
  desc: string;
  attemptsLabel: string;
  /** Default attempts, used as the placeholder when empty */
  defAttempts: number;
  /** Default interval policy, used as the placeholder when empty */
  defInterval: string;
  /** Meaning of -1 attempts */
  offHint: string;
};

export const RETRY_LAYERS = {
  connect: {
    title: "Connection retries",
    where: "SDK · Before HTTP 200",
    trigger:
      "Connection failure or no HTTP 200 yet: connection resets, read/write timeouts, DNS failures and other network errors, plus HTTP 408, 429, 500, 502, 503 and 504.",
    skips: "Other statuses (400 / 401 / 403 / 404 / 413 / 422, etc.) are deterministic rejections; retries would fail again, so they propagate immediately.",
    desc: "Resends the same request unchanged. Once streaming starts (HTTP 200 received), disconnections are handled by other layers.",
    attemptsLabel: "Retry attempts",
    defAttempts: 3,
    defInterval: "0.5s → 1s → 2s exponential (8s cap)",
    offHint: "-1 = no retries; propagate failure immediately",
  },
  empty: {
    title: "Empty-response retries",
    where: "SDK · OpenAI format only",
    trigger:
      "HTTP 200 with normal stop finish_reason but no content blocks: possible causes include empty gateway frames, dropped reasoning fields or sampling failures.",
    skips: "Excludes empty content caused by max_tokens truncation; raise the output limit instead of repeating the same failure.",
    desc: "Resends the entire prompt, which is expensive with long contexts; keep the retry count low.",
    attemptsLabel: "Retry attempts",
    defAttempts: 2,
    defInterval: "0.5s → 1s → 2s exponential (8s cap)",
    offHint: "-1 = return empty responses unchanged",
  },
  stream: {
    title: "Same-provider safe-window retries",
    where: "Application · Before delivering any output",
    trigger:
      "Errors after streaming starts (HTTP 200): disconnections, provider overloaded errors, or in-stream 429 / 5xx events, before any token reaches the caller.",
    skips:
      "No retry for exhausted quota (402 / insufficient_quota; fail over to another profile), excessive context (413 / context length; use compaction), or deterministic 400 / 401 / 403 / 404 / 422 rejections.",
    desc: "Replays the same request on the same profile. With no output delivered, replay cannot duplicate model output or tool execution.",
    attemptsLabel: "Retry attempts",
    defAttempts: 2,
    defInterval: "0.5s → 1s exponential (4s cap)",
    offHint: "-1 = send stream failures directly to outer intent reruns",
  },
  breaker: {
    title: "Failover circuit breaker",
    where: "Application · Process-wide global policy",
    trigger:
      "Consecutive transient failures (429, 5xx, network errors) open the circuit at the threshold. Deterministic failures such as insufficient credit (402), invalid keys (401 / 403) or missing models (404) open it immediately.",
    skips: "One success resets the counter, so occasional failures do not accumulate indefinitely.",
    desc: "After tripping, failover skips this profile during cooldown. State is persisted and survives restarts.",
    attemptsLabel: "Consecutive failures before tripping",
    defAttempts: 3,
    defInterval: "1min → 5min → 30min ladder",
    offHint: "-1 = transient failures never trip the breaker (deterministic failures still do)",
  },
  intent: {
    title: "Intent reruns",
    where: "Application · Process-wide global policy",
    trigger:
      "A worker ends with model_error after inner retries are exhausted, or the stream disconnects after output delivery (when replay is unsafe and the whole intent must restart).",
    skips: "Quota exhaustion is handled by profile failover, not reruns. Paused, terminated or finalizing tasks yield immediately without waiting through backoff.",
    desc: "Runs the entire intent again from the beginning. As the outermost layer, each rerun multiplies inner retry attempts.",
    attemptsLabel: "Rerun attempts",
    defAttempts: 2,
    defInterval: "Fixed 3s",
    offHint: "-1 = no reruns; mark the intent blocked",
  },
} satisfies Record<string, LayerMeta>;

type LayerKey = keyof typeof RETRY_LAYERS;

/** Human-readable milliseconds displayed beside inputs. */
function humanMs(ms: number) {
  if (!Number.isFinite(ms) || ms <= 0) return "";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${Number((ms / 1000).toFixed(2))}s`;
  return `${Number((ms / 60_000).toFixed(2))}min`;
}

/** Controlled numeric input: empty string maps to 0; intermediate values ("-", "1e") stay local without updating the parent. */
function NumField({
  id,
  value,
  onChange,
  placeholder,
  min,
}: {
  id: string;
  value: number;
  onChange: (n: number) => void;
  placeholder: string;
  min: number;
}) {
  const [text, setText] = React.useState(value === 0 ? "" : String(value));
  // Follow parent changes after loading policies or switching profiles; local typing does not reach this branch
  // because value already equals the parsed local text.
  React.useEffect(() => {
    const incoming = value === 0 ? "" : String(value);
    setText((cur) => (Number(cur || 0) === value ? cur : incoming));
  }, [value]);
  return (
    <Input
      id={id}
      type="number"
      min={min}
      className="w-28 shrink-0"
      value={text}
      placeholder={placeholder}
      onChange={(e) => {
        setText(e.target.value);
        const n = Number(e.target.value);
        onChange(e.target.value.trim() === "" || !Number.isFinite(n) ? 0 : Math.trunc(n));
      }}
    />
  );
}

/** Two controls per retry layer. idPrefix keeps label htmlFor references unique on a page. */
export function RetryRuleFields({
  layer,
  idPrefix,
  value,
  onChange,
  compact,
}: {
  layer: LayerKey;
  idPrefix: string;
  value: LLMRetryRule;
  onChange: (r: LLMRetryRule) => void;
  /** true = compact profile drawer view: omit expanded explanations except trigger conditions */
  compact?: boolean;
}) {
  const meta = RETRY_LAYERS[layer];
  const human = humanMs(value.interval_ms);
  return (
    <div className={compact ? "grid gap-2" : "grid gap-3 rounded-lg border p-3"}>
      <div className="grid gap-0.5">
        <div className="flex flex-wrap items-baseline gap-2">
          <Label className="text-sm">{meta.title}</Label>
          <span className="text-muted-foreground text-xs">{meta.where}</span>
        </div>
        {/* Show exact errors and status codes handled here; otherwise users may expect controls to affect unrelated failures. */}
        <p className="text-muted-foreground text-xs">
          <span className="font-medium text-foreground">Triggers</span>：{meta.trigger}
        </p>
        {!compact && meta.skips && (
          <p className="text-muted-foreground text-xs">
            <span className="font-medium text-foreground">Not handled</span>：{meta.skips}
          </p>
        )}
        {!compact && <p className="text-muted-foreground text-xs">{meta.desc}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex items-center gap-2">
          <Label htmlFor={`${idPrefix}-${layer}-n`} className="text-muted-foreground text-xs">
            {meta.attemptsLabel}
          </Label>
          <NumField
            id={`${idPrefix}-${layer}-n`}
            min={-1}
            value={value.attempts}
            placeholder={`Default ${meta.defAttempts}`}
            onChange={(n) => onChange({ ...value, attempts: n })}
          />
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor={`${idPrefix}-${layer}-ms`} className="text-muted-foreground text-xs">
            Interval (ms)
          </Label>
          <NumField
            id={`${idPrefix}-${layer}-ms`}
            min={0}
            value={value.interval_ms}
            placeholder="Default backoff"
            onChange={(n) => onChange({ ...value, interval_ms: n })}
          />
          <span className="text-muted-foreground text-xs">{human ? `Fixed ${human}` : meta.defInterval}</span>
        </div>
      </div>
      {!compact && <p className="text-muted-foreground text-xs">Empty = default; {meta.offHint}。</p>}
    </div>
  );
}

/** Three endpoint-specific overrides in the model profile drawer. */
export function ProfileRetryFields({
  value,
  onChange,
}: {
  value: LLMRetryOverride;
  onChange: (o: LLMRetryOverride) => void;
}) {
  return (
    <div className="grid gap-3 rounded-lg border p-3">
      <div className="grid gap-0.5">
        <Label className="text-sm">Retry overrides</Label>
        <p className="text-muted-foreground text-xs">
          Applies only to this profile, overriding global Retry and backoff defaults. Empty = global default; -1 attempts disables that layer.
          A specified interval replaces exponential backoff with a fixed interval. Circuit breaking and intent reruns are process-wide and configurable only globally.
        </p>
      </div>
      {(["connect", "empty", "stream"] as const).map((k) => (
        <div key={k} className="border-t pt-3 first:border-t-0 first:pt-0">
          <RetryRuleFields
            compact
            layer={k}
            idPrefix="pf"
            value={value[k]}
            onChange={(r) => onChange({ ...value, [k]: r })}
          />
        </div>
      ))}
    </div>
  );
}

/** Retry and backoff tab: global defaults for all five layers. */
export function RetryPolicyPanel() {
  const [policy, setPolicy] = React.useState<LLMRetryPolicy>(ZERO_POLICY);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const p = await api.llmRetryPolicy();
      setPolicy({ ...ZERO_POLICY, ...p });
    } catch (e) {
      toast.error(`Failed to load retry policy: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    if (saving) return;
    setSaving(true);
    try {
      // The backend clamps out-of-range values and returns them; refresh from that response so the UI shows saved values.
      const saved = await api.saveLLMRetryPolicy(policy);
      setPolicy({ ...ZERO_POLICY, ...saved });
      toast.success("Saved; effective immediately (in-flight calls keep their old parameters)");
    } catch (e) {
      toast.error(`Save failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  const set = (k: LayerKey) => (r: LLMRetryRule) => setPolicy((p) => ({ ...p, [k]: r }));

  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed p-10 text-muted-foreground text-sm">
        <Loader2Icon className="size-4 animate-spin" /> Loading retry policy…
      </div>
    );
  }

  return (
    <div className="grid gap-4">
      <div className="rounded-lg border bg-muted/30 p-3 text-muted-foreground text-xs leading-relaxed">
        A failed model call passes through five retry layers from inner to outer:
        <span className="text-foreground"> connection → empty response → same-provider safe window → failover circuit breaker → intent reruns</span>
        . Outer layers run only after inner layers are exhausted, so attempt counts are
        <span className="text-foreground">multiplied</span>
        . Maximizing every layer can turn a brief failure into dozens of requests.
        Leave everything empty for existing defaults. The first three layers can be overridden per model profile.
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(Object.keys(RETRY_LAYERS) as LayerKey[]).map((k) => (
          <RetryRuleFields key={k} layer={k} idPrefix="gl" value={policy[k]} onChange={set(k)} />
        ))}
      </div>

      <div className="flex gap-2">
        <Button onClick={save} disabled={saving}>
          {saving ? <Loader2Icon className="animate-spin" /> : <SaveIcon />}
          Save
        </Button>
        <Button variant="outline" onClick={() => setPolicy(ZERO_POLICY)} disabled={saving}>
          Restore all defaults
        </Button>
      </div>
    </div>
  );
}
