"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { http } from "@/lib/api";
import { Button } from "@/components/ui/button";

type Config = { origin: string; model: string; resources: string[]; campaigns: string[]; running: number; poll_error: string | null };
type Campaign = {
  task: { id: string; status: string; completed_at?: string };
  manifest: { generation_id: string } | null;
  report: { verified: boolean; report: string; reason: string } | null;
  observations: { resource: string; status: number }[];
  retained: boolean[];
};

function ReconPageInner() {
  const router = useRouter();
  const selected = useSearchParams().get("id");
  const [config, setConfig] = useState<Config | null>(null);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let pending = false;
    const update = async () => {
      if (pending) return;
      pending = true;
      try {
        if (selected !== null && !/^[1-9][0-9]{0,18}$/.test(selected)) throw new Error("Invalid campaign ID");
        const next = await http<Config>("/recon");
        const id = selected ?? next.campaigns[0];
        const detail = id ? await http<Campaign>(`/recon/campaigns/${encodeURIComponent(id)}`) : null;
        if (active) { setConfig(next); setCampaign(detail); setError(""); }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "Campaign read failed");
      } finally { pending = false; }
    };
    void update();
    const timer = setInterval(update, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [selected]);
  async function start() {
    setBusy(true); setError("");
    try {
      const result = await http<{ task_id: string }>("/recon/campaigns", { method: "POST", body: "{}" }, 201);
      router.replace(`/function/recon?id=${encodeURIComponent(result.task_id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Campaign outcome is unknown; refresh before retrying.");
    } finally { setBusy(false); }
  }
  return <main className="mx-auto w-full max-w-5xl space-y-6 p-6">
    <div className="space-y-2"><h1 className="text-2xl font-semibold">ServiceNow reconnaissance</h1>
      <p className="text-muted-foreground">Read-only inventory of the configured developer instance.</p></div>
    {error && <p role="alert" className="rounded border border-destructive p-3 text-destructive">{error}</p>}
    {config && <section className="space-y-3 rounded-lg border p-5">
      <p><strong>Target:</strong> {config.origin}</p><p><strong>Model:</strong> {config.model}</p>
      <p className="text-sm text-muted-foreground">Inventory: {config.resources.join(", ")}. One request per second. Credentials are supplied privately by the operator.</p>
      <Button disabled={busy || config.running > 0 || !!config.poll_error} onClick={start}>{busy ? "Starting…" : config.running ? "Campaign running" : "Start read-only campaign"}</Button>
      {config.campaigns.length > 0 && <div className="flex flex-wrap gap-2">{config.campaigns.map(id =>
        <Button variant="outline" key={id} onClick={() => router.replace(`/function/recon?id=${encodeURIComponent(id)}`)}>Task {id}</Button>)}</div>}
    </section>}
    {campaign && (selected === null || campaign.task.id === selected) && <section className="space-y-4 rounded-lg border p-5">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold">Task {campaign.task.id}: {campaign.task.status}</h2>
        <Link className="underline" href="/function/tasks">All tasks</Link></div>
      <p className="text-sm text-muted-foreground">Generation {campaign.manifest?.generation_id ?? "being admitted"}</p>
      <div className="flex flex-wrap gap-2">{campaign.observations.map(o => <span className="rounded border px-2 py-1 text-sm" key={o.resource}>{o.resource}: {o.status ? `HTTP ${o.status}` : "not requested"}</span>)}</div>
      {campaign.report && <><p><strong>Inventory verified:</strong> {campaign.report.verified ? "Yes" : "No"}</p>
        <pre className="overflow-auto whitespace-pre-wrap rounded bg-muted p-4 font-sans text-sm">{campaign.report.report}</pre></>}
      {campaign.task.completed_at && <p className="text-sm text-muted-foreground">Completed {campaign.task.completed_at}. Generation retained: {campaign.retained.some(Boolean) ? "yes" : "no"}.</p>}
    </section>}
  </main>;
}

export default function ReconPage() {
  return <Suspense fallback={null}><ReconPageInner /></Suspense>;
}
