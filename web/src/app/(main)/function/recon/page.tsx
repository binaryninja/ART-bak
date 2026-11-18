"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
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

export default function ReconPage() {
  const [config, setConfig] = useState<Config | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    const next = await http<Config>("/recon");
    setConfig(next);
    const id = selected ?? next.campaigns[0];
    if (id) setCampaign(await http<Campaign>(`/recon/campaigns/${encodeURIComponent(id)}`));
  }, [selected]);
  useEffect(() => {
    let active = true;
    const update = () => refresh().catch(e => { if (active) setError(e.message); });
    void update();
    const timer = setInterval(update, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [refresh]);
  async function start() {
    setBusy(true); setError("");
    try {
      const result = await http<{ task_id: string }>("/recon/campaigns", { method: "POST", body: "{}" }, 201);
      setSelected(result.task_id);
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
        <Button variant="outline" key={id} onClick={() => setSelected(id)}>Task {id}</Button>)}</div>}
    </section>}
    {campaign && <section className="space-y-4 rounded-lg border p-5">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold">Task {campaign.task.id}: {campaign.task.status}</h2>
        <Link className="underline" href={`/function/tasks/detail?id=${campaign.task.id}`}>Task details</Link></div>
      <p className="text-sm text-muted-foreground">Generation {campaign.manifest?.generation_id ?? "being admitted"}</p>
      <div className="flex flex-wrap gap-2">{campaign.observations.map(o => <span className="rounded border px-2 py-1 text-sm" key={o.resource}>{o.resource}: {o.status ? `HTTP ${o.status}` : "not requested"}</span>)}</div>
      {campaign.report && <><p><strong>Inventory verified:</strong> {campaign.report.verified ? "Yes" : "No"}</p>
        <pre className="overflow-auto whitespace-pre-wrap rounded bg-muted p-4 font-sans text-sm">{campaign.report.report}</pre></>}
      {campaign.task.completed_at && <p className="text-sm text-muted-foreground">Completed {campaign.task.completed_at}. Generation retained: {campaign.retained.some(Boolean) ? "yes" : "no"}.</p>}
    </section>}
  </main>;
}
