import type { LogLine } from "./types";

export const LOG_ROW_LIMIT: number;
export interface LogHistoryState {
  lines: LogLine[];
  hasMore: boolean;
  loadingHistory: boolean;
  error: string | null;
}
export function logRowKey(line: LogLine): string;
export function oldestLogId(lines: LogLine[]): number;
export function mergeLogRows(previous: LogLine[], incoming: LogLine[], prepend?: boolean, limit?: number): LogLine[];
export function emptyLogHistory(): LogHistoryState;
export function createLogHistory(callbacks: {
  read: (path: string, signal: AbortSignal) => Promise<unknown>;
  onChange: (state: LogHistoryState) => void;
  limit?: number;
}): {
  activate(): void;
  deactivate(): void;
  clear(): void;
  append(line: unknown): boolean;
  loadOlder(): boolean;
};
