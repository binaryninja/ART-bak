export const DEFAULT_HOME: "/function/tasks";
export const SKILLS_HOME: "/system/skills";
export function signInLanding(profile: string | undefined, mock: boolean): "/" | typeof DEFAULT_HOME;
export function readLandingProfile(token: string, signal: AbortSignal): Promise<unknown>;
export function startHostLanding(options: {
  readProfile: (token: string, signal: AbortSignal) => Promise<unknown>;
  getToken: () => string | null;
  navigate: (destination: typeof DEFAULT_HOME | typeof SKILLS_HOME) => void;
  onError: (message: string) => void;
  timeoutMs?: number;
}): () => void;
