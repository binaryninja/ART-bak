"use client";
import { useState } from "react";
import { http } from "@/lib/api";

type SourceRef = { line: number; expression: string };
type Case = { case_id: string; description: string; path_params: unknown; query: unknown; body: unknown; expected: unknown; source_evidence: SourceRef; safety: string; fixture_refs: string[] };
type Operation = { operation_id: string; name: string; method: string; route: string; source_sha256?: string; source: string; contract: unknown; cases: Case[]; limitations: string[] };
type Request = { case_id: string; method: string; path: string; authentication: string; status: number; outcome: string; body_sample: string; body_sha256: string };
type Source = { records: Operation[]; fixtures: unknown; helpers: unknown };
type Probes = { requests: Request[]; skipped?: unknown[] };
const json = (value: unknown) => JSON.stringify(value, null, 2);

export function PublicRestEvidence({ taskId }: { taskId: string }) {
  const [value, setValue] = useState<{source: Source; probes: Probes} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function load() {
    setBusy(true); setError("");
    try {
      const base = `/tasks/${encodeURIComponent(taskId)}/assessment/evidence/`;
      const [source, probes] = await Promise.all([http<Source>(base + "public_rest_source"), http<Probes>(base + "public_rest_probes")]);
      setValue({source, probes});
    } catch (e) { setError(e instanceof Error ? e.message : "Evidence read failed"); }
    finally { setBusy(false); }
  }
  return <section className="space-y-3 rounded-lg border p-4"><h2 className="font-semibold">Source-informed REST tests</h2>
    <p className="text-sm text-muted-foreground">Inspect the input contract, exact test parameters, source branch, and recorded response for each selected endpoint.</p>
    <button className="text-sm underline" disabled={busy} onClick={() => void load()}>{busy ? "Loading…" : value ? "Refresh test evidence" : "Load test evidence"}</button>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {value && <><p className="text-sm">{value.source.records.length} selected operations · {value.source.records.reduce((n, r) => n + r.cases.length, 0)} source-derived cases · {value.probes.requests.length} recorded requests</p>
      {value.source.records.map(op => <details key={op.operation_id} className="rounded border p-3"><summary className="cursor-pointer font-medium">{op.method} {op.route} · {op.name}</summary>
        <p className="my-2 break-all text-xs">Source SHA-256: {op.source_sha256 || "unavailable"}</p>
        <details><summary className="cursor-pointer text-sm">Input contract and handler source</summary><pre className="my-2 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">{json(op.contract)}</pre><pre className="overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">{op.source || "Protected or unavailable"}</pre></details>
        {op.cases.map(test => <details key={test.case_id} className="mt-3 rounded border p-3"><summary className="cursor-pointer">{test.case_id.split(":").at(-1)} · {test.description}</summary>
          <p className="my-2 text-xs">Source line {test.source_evidence.line}: <code>{test.source_evidence.expression}</code> · {test.safety}</p>
          <pre className="overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">{json({path: test.path_params, query: test.query, body: test.body, expected: test.expected, fixtures: test.fixture_refs})}</pre>
          {value.probes.requests.filter(r => r.case_id === test.case_id).map((r, i) => <details key={i} className="mt-2"><summary className="cursor-pointer break-all text-sm">{r.authentication} · HTTP {r.status} · {r.outcome} · {r.method} {r.path}</summary><p className="my-2 break-all text-xs">Response SHA-256: {r.body_sha256}</p><pre className="overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">{r.body_sample}</pre></details>)}
        </details>)}
        {!!op.limitations.length && <ul className="mt-3 list-disc pl-5 text-sm">{op.limitations.map((s,i) => <li key={i}>{s}</li>)}</ul>}
        {!op.cases.length && value.probes.requests.filter(r => r.case_id === op.operation_id + ":metadata").map((r,i) => <p key={i} className="mt-2 break-all text-sm">{r.authentication} · {r.method} {r.path} · HTTP {r.status}</p>)}
      </details>)}
      <details><summary className="cursor-pointer text-sm">Helper source and fixture provenance</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">{json({helpers:value.source.helpers,fixtures:value.source.fixtures,skipped:value.probes.skipped})}</pre></details>
    </>}
  </section>;
}
