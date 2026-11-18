"use client";
import { type ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { Markdown } from "@/components/markdown";
import { http } from "@/lib/api";
import type { Task } from "@/lib/types";

type Evidence = { status?: number; error?: string; requested?: boolean; record_count?: number; completeness?: string; required_capability?: string; [key: string]: unknown };
type Detail = { id: string; origin: string; definition: string; version: number; state: string; task: Task;
  observations: Record<string, Evidence>; sources: unknown[]; retained: boolean[];
  exploration_id?: number; tool_calls?: Record<string, number>;
  sessions?: { session_id: string; worker: string; calls: number }[];
  report?: { summary?: string; run_id?: number; capabilities?: { access_level?: string; authenticated?: boolean; roles?: string[] } } };

export function AssessmentDetail({ task, lineage }: { task: Task; lineage?: ReactNode }) {
  const [showLineage, setShowLineage] = useState(false);
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState<string | null>(null);
  async function downloadEvidence(operation: string) {
    setDownloading(operation);
    try {
      const evidence = await http<Evidence>(`/tasks/${encodeURIComponent(task.id)}/assessment/evidence/${encodeURIComponent(operation)}`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(evidence, null, 2) + "\n"], { type: "application/json" }));
      const link = document.createElement("a"); link.href = url; link.download = `task-${task.id}-${operation}.json`; link.click(); URL.revokeObjectURL(url);
    } catch (e) { setError(e instanceof Error ? e.message : "Evidence download failed"); }
    finally { setDownloading(null); }
  }
  useEffect(() => {
    let active = true, pending = false;
    const refresh = async () => {
      if (pending) return; pending = true;
      try { const value = await http<Detail>(`/tasks/${encodeURIComponent(task.id)}/assessment?summary=1`); if (active) { setData(value); setError(""); } }
      catch (e) { if (active) setError(e instanceof Error ? e.message : "Assessment read failed"); }
      finally { pending = false; }
    };
    void refresh(); const timer = setInterval(refresh, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [task.id]);
  return <main className="flex-1 space-y-5 p-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><Link className="text-sm underline" href="/function/tasks">All tasks</Link><h1 className="text-xl font-semibold">{task.name || task.description}</h1></div><Link className="underline" href="/function/assessments">Assessments</Link></div>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {!data ? <p>Loading assessment…</p> : <>
      <section className="space-y-2 rounded-lg border p-4"><p><strong>Task {data.task.id}:</strong> {data.task.status} · {data.origin}</p><p className="text-sm text-muted-foreground">{data.definition} v{data.version} · {data.id}</p><p>Access: {data.report?.capabilities?.access_level ?? "being assessed"}</p>{data.task.completed_at && <p>Completed {data.task.completed_at}. Generation retained: {data.retained.some(Boolean) ? "yes" : "no"}.</p>}</section>
      <section className="space-y-3 rounded-lg border p-4"><h2 className="font-semibold">Evidence</h2>{Object.entries(data.observations).map(([name, evidence]) => <details key={name}><summary className="cursor-pointer">{name}: {evidence.status ? `HTTP ${evidence.status}` : evidence.error || "not requested"}{evidence.record_count != null && ` · ${evidence.record_count} records (${evidence.completeness || "collected"})`}</summary><button className="mt-2 text-sm underline" disabled={downloading!==null} onClick={() => void downloadEvidence(name)}>{downloading===name ? "Downloading…" : "Download full evidence JSON"}</button><pre className="mt-2 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">{JSON.stringify(evidence, null, 2)}</pre></details>)}{!Object.keys(data.observations).length && <p className="text-sm text-muted-foreground">No observations recorded yet.</p>}</section>
      {data.report && <section className="space-y-3 rounded-lg border p-4"><h2 className="font-semibold">Report</h2><Markdown text={data.report.summary ?? ""} /></section>}
      <section className="space-y-2 rounded-lg border p-4"><h2 className="font-semibold">Execution sessions</h2>{data.exploration_id && <p><Link className="text-sm underline" href={`/function/commands?task=${data.exploration_id}`}>Open tool execution history</Link> · {Object.values(data.tool_calls ?? {}).reduce((sum, value) => sum + value, 0)} recorded calls</p>}{data.sessions?.map(s => <p key={s.session_id} className="text-sm"><code>{s.session_id}</code> · {s.worker} · {s.calls} recorded provider calls</p>)}<Link className="text-sm underline" href={`/function/llm-records?task=${encodeURIComponent(task.id)}`}>Open provider recordings</Link></section>
      {lineage && <section className="space-y-3 rounded-lg border p-4"><button className="font-semibold underline" onClick={() => setShowLineage(v => !v)}>{showLineage ? "Hide" : "Show"} exploration lineage</button>{showLineage && lineage}</section>}
      <details className="rounded-lg border p-4"><summary className="cursor-pointer font-semibold">Discovery provenance</summary><pre className="mt-3 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(data.sources, null, 2)}</pre></details>
    </>}
  </main>;
}
