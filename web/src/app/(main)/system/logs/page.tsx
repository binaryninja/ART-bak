"use client";

import * as React from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { http, sseUrl } from "@/lib/api";
import { createLogHistory, emptyLogHistory, LOG_ROW_LIMIT, logRowKey } from "@/lib/log-history-state.mjs";
import { MOCK } from "@/lib/mock/enabled";
import type { LogLine } from "@/lib/types";
import { cn } from "@/lib/utils";

// Mock demo: sample logs without a backend SSE stream.
const MOCK_LOGS: LogLine[] = [
  { seq: 1, ts: "2026-07-26T03:55:00Z", level: "info", tag: "engine", text: "ARTEX v0.1.0 backend listening on :8787 (workers=3)" },
  { seq: 2, ts: "2026-07-26T03:55:01Z", level: "info", tag: "config", text: "LLM configured from DB: anthropic / claude-opus-4-8" },
  { seq: 3, ts: "2026-07-26T03:56:10Z", level: "info", tag: "planner", text: "task t-acme-web: planning round 3, created intent i-4" },
  { seq: 4, ts: "2026-07-26T03:57:00Z", level: "warn", tag: "guard", text: "block bash: out-of-scope target: out.evil.example is outside scope" },
  { seq: 5, ts: "2026-07-26T03:57:30Z", level: "info", tag: "work#1", text: "report_finding: Default Credentials (high) saved to database" },
  { seq: 6, ts: "2026-07-26T03:58:20Z", level: "error", tag: "work#3", text: "intercept: mysqldump matched a destructive-action rule; awaiting human approval" },
];

const levelTone: Record<LogLine["level"], string> = {
  info: "text-foreground/80",
  warn: "text-amber-600 dark:text-amber-400",
  error: "text-red-600 dark:text-red-400",
};
const levelDot: Record<LogLine["level"], string> = {
  info: "bg-muted-foreground/40",
  warn: "bg-amber-500",
  error: "bg-red-500",
};

function fmtTime(ts: string) {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export default function LogsPage() {
  const [historyState, setHistoryState] = React.useState(emptyLogHistory);
  const { lines, hasMore, loadingHistory, error } = historyState;
  const history = React.useMemo(() => createLogHistory({
    read: (path, signal) => http(path, { signal }),
    onChange: setHistoryState,
  }), []);
  const [q, setQ] = React.useState("");
  const [level, setLevel] = React.useState<"all" | LogLine["level"]>("all");
  const [paused, setPaused] = React.useState(false);
  const bottom = React.useRef<HTMLDivElement>(null);
  const stick = React.useRef(true);
  const pausedRef = React.useRef(false);
  pausedRef.current = paused;

  // SSE replays the ring on each connection. Rows can gain db_id after the
  // original live frame; history and replay therefore share both identities.
  React.useEffect(() => {
    history.activate();
    if (MOCK) {
      for (const line of MOCK_LOGS) history.append(line);
      return () => history.deactivate();
    }
    let stopped = false;
    const es = new EventSource(sseUrl("/api/logs/stream?since=0"));
    es.onmessage = (e) => {
      if (stopped || pausedRef.current) return;
      try {
        history.append(JSON.parse(e.data));
      } catch {
        /* ignore malformed frame */
      }
    };
    return () => {
      stopped = true;
      es.close();
      history.deactivate();
    };
  }, [history]);

  const filtered = React.useMemo(() => {
    const needle = q.trim().toLowerCase();
    return lines.filter(
      (l) =>
        (level === "all" || l.level === level) &&
        (!needle || l.text.toLowerCase().includes(needle) || l.tag.toLowerCase().includes(needle)),
    );
  }, [lines, q, level]);

  React.useEffect(() => {
    if (stick.current && !paused) bottom.current?.scrollIntoView();
  }, [filtered, paused]);

  const counts = React.useMemo(() => {
    let warn = 0,
      error = 0;
    for (const l of lines) {
      if (l.level === "warn") warn++;
      else if (l.level === "error") error++;
    }
    return { warn, error, total: lines.length };
  }, [lines]);

  return (
    <div className="flex flex-1 flex-col gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">System logs</h1>
        <p className="text-muted-foreground text-sm">Live backend logs (planner / worker / database / traffic …)</p>
      </div>
      <div className="flex flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            placeholder="Filter (text / tag)…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="h-8 max-w-xs"
          />
          <div className="flex gap-1">
            {(["all", "info", "warn", "error"] as const).map((lv) => (
              <Button
                key={lv}
                size="sm"
                variant={level === lv ? "default" : "outline"}
                className="h-8"
                onClick={() => setLevel(lv)}
              >
                {lv === "all" ? "All" : lv}
              </Button>
            ))}
          </div>
          <Button
            size="sm"
            variant={paused ? "default" : "outline"}
            className="h-8"
            onClick={() => setPaused((p) => !p)}
          >
            {paused ? "Paused" : "Pause"}
          </Button>
          <Button size="sm" variant="outline" className="h-8" onClick={() => history.clear()}>
            Clear
          </Button>
          <span className="ml-auto text-xs text-muted-foreground">
            {counts.total} lines ·{" "}
            <span className="text-amber-600 dark:text-amber-400">{counts.warn} warnings</span> ·{" "}
            <span className="text-red-600 dark:text-red-400">{counts.error} errors</span>
          </span>
        </div>

        <div
          onScroll={(e) => {
            const el = e.currentTarget;
            stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          }}
          className="h-[calc(100vh-14rem)] overflow-auto rounded-lg border bg-card p-2 font-mono text-xs leading-relaxed"
        >
          {!MOCK && hasMore && lines.length < LOG_ROW_LIMIT && (
            <div className="flex justify-center py-2">
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                disabled={loadingHistory}
                onClick={() => history.loadOlder()}
              >
                {loadingHistory ? "Loading…" : "Load earlier logs"}
              </Button>
            </div>
          )}
          {error && <p role="alert" className="py-2 text-center text-destructive">{error}</p>}
          {lines.length >= LOG_ROW_LIMIT && (
            <p className="py-2 text-center text-muted-foreground">
              5,000-line limit reached. Clear the view to start a new window.
            </p>
          )}
          {filtered.length === 0 ? (
            <p className="py-10 text-center text-muted-foreground">No logs yet.</p>
          ) : (
            filtered.map((l) => {
              const body =
                l.tag && l.text.startsWith("[" + l.tag + "]")
                  ? l.text.slice(l.tag.length + 2).trimStart()
                  : l.text;
              return (
                <div key={logRowKey(l)} className="flex items-start gap-2 px-1 py-0.5 hover:bg-muted/40">
                  <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", levelDot[l.level])} />
                  <span className="shrink-0 tabular-nums text-muted-foreground">{fmtTime(l.ts)}</span>
                  {l.tag && (
                    <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-foreground/70">{l.tag}</span>
                  )}
                  <span className={cn("min-w-0 whitespace-pre-wrap break-words", levelTone[l.level])}>{body}</span>
                </div>
              );
            })
          )}
          <div ref={bottom} />
        </div>
      </div>
    </div>
  );
}
