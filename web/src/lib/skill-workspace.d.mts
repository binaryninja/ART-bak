import type { SkillItem } from "./types";
export function skillFileKey(skill: string, path: string): string;
export function skillResourcePath(skill: string, path?: string): string;
export function skillDeletionPath(skill: string, path: string): string;
export interface SkillWorkspaceProfile {
  profile: "skills-workspace";
  writes_enabled: boolean;
  deletes_enabled?: boolean;
}
export function confirmedSkillWorkspaceProfile(value: unknown): SkillWorkspaceProfile;
export interface SkillCreationInput {
  name: string;
  description: string;
  license?: string;
  compatibility?: string;
  instructions?: string;
  mcps?: string[];
}
export function validSkillCreationName(name: unknown): boolean;
export function confirmedSkillCreation(value: unknown, name: string): { name: string };
export function confirmedSkillWrite(value: unknown): { ok: true };
export function confirmedSkillDirectory(value: unknown, path: string): { dir: string };
export function confirmedSkillDeletion(value: unknown, path: string): { deleted: string };
export function confirmedSkillContent(value: unknown, path: string): string;
export interface SkillDraft {
  content: string;
  baseline: string;
  revision: number;
  loaded: boolean;
  loading: boolean;
  dirty: boolean;
  deleted: boolean;
  error: string | null;
}
export interface SkillWorkspaceSnapshot {
  drafts: ReadonlyMap<string, SkillDraft>;
  pending: ReadonlyMap<string, { path: string; kind: string }>;
  uncertain: ReadonlyMap<string, { path: string; kind: string }>;
  errors: ReadonlyMap<string, { path: string; message: string }>;
  rootPending: { name: string } | null;
  rootUncertain: { name: string; message: string } | null;
  rootError: { name: string; message: string } | null;
  profile: {
    status: "unchecked" | "loading" | "ready" | "error";
    writesEnabled: boolean;
    deletesEnabled: boolean;
    error: string | null;
  } | null;
  writeModeEnabled: boolean;
  deleteModeEnabled: boolean;
  deletionPending: { path: string; kind: string } | null;
  deletionUncertain: { skill: string; path: string; kind: string; message: string } | null;
  deletedPaths: ReadonlyMap<string, { skill: string; path: string }>;
}
export interface SkillWorkspaceApi {
  skillWorkspaceProfile?(signal?: AbortSignal): Promise<unknown>;
  createSkill(input: SkillCreationInput): Promise<unknown>;
  readSkillFile(skill: string, path: string): Promise<string>;
  writeSkillFile(skill: string, path: string, content: string): Promise<unknown>;
  deleteSkillPath?(skill: string, path: string): Promise<unknown>;
  createSkillDir(skill: string, path: string): Promise<unknown>;
}
export function createSkillWorkspace(
  api: SkillWorkspaceApi,
  options?: { requireProfile?: boolean },
): {
  subscribe(listener: () => void): () => void;
  getSnapshot(): SkillWorkspaceSnapshot;
  readProfile(): () => void;
  read(skill: string, path: string): () => void;
  edit(skill: string, path: string, content: string): void;
  createSkill(input: SkillCreationInput): Promise<{ name: string } | null>;
  remove(skill: string, path: string): Promise<boolean>;
  save(skill: string, path: string): Promise<boolean>;
  create(skill: string, path: string, kind: "file" | "dir"): Promise<boolean>;
};

export function confirmedSkillCatalog(value: unknown): SkillItem[];
