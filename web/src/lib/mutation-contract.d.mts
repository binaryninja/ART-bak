export class ApiError extends Error {
  constructor(status: number, payload: unknown, fallback: string);
  status: number; pending: boolean; unknown: boolean; retry: false | undefined;
}
export function mutationMustWait(error: unknown): boolean;
export function isPythonProfile(value: unknown): boolean;
export function toolsVersionHeaders(digest?: string, required?: boolean): Record<string, string>;
export function ruleVersionHeaders(version: string): Record<string, string>;
export function confirmedIntentState(result: unknown, action: string): 'paused' | 'open' | null;
