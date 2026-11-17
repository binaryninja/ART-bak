import type { SkillItem } from "./types";
export function skillFileKey(skill: string, path: string): string;
export function skillResourcePath(skill: string, path?: string): string;
export function confirmedSkillWrite(value: unknown): { ok: true };
export function confirmedSkillDirectory(value: unknown, path: string): { dir: string };
export function confirmedSkillContent(value: unknown, path: string): string;
export interface SkillDraft {
  content: string;
  baseline: string;
  revision: number;
  loaded: boolean;
  loading: boolean;
  dirty: boolean;
  error: string | null;
}
export interface SkillWorkspaceSnapshot {
  drafts: ReadonlyMap<string, SkillDraft>;
  pending: ReadonlyMap<string, { path: string; kind: string }>;
  uncertain: ReadonlyMap<string, { path: string; kind: string }>;
  errors: ReadonlyMap<string, { path: string; message: string }>;
}
export interface SkillWorkspaceApi {
  readSkillFile(skill: string, path: string): Promise<string>;
  writeSkillFile(skill: string, path: string, content: string): Promise<unknown>;
  createSkillDir(skill: string, path: string): Promise<unknown>;
}
export function createSkillWorkspace(api: SkillWorkspaceApi): {
  subscribe(listener: () => void): () => void;
  getSnapshot(): SkillWorkspaceSnapshot;
  read(skill: string, path: string): () => void;
  edit(skill: string, path: string, content: string): void;
  save(skill: string, path: string): Promise<boolean>;
  create(skill: string, path: string, kind: "file" | "dir"): Promise<boolean>;
};

export function confirmedSkillCatalog(value: unknown): SkillItem[];
