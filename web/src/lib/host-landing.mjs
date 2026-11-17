// Fixed navigation only. Host discovery never grants authority or calls task APIs.
import { ApiError } from "./mutation-contract.mjs";
import { confirmedSkillWorkspaceProfile } from "./skill-workspace.mjs";

export const DEFAULT_HOME = "/function/tasks";
export const SKILLS_HOME = "/system/skills";

export function signInLanding(profile, mock) {
  return profile === "python" && !mock ? "/" : DEFAULT_HOME;
}

// This landing-specific GET deliberately has no credential-clearing or redirect
// side effects. A retired 401 must not sign out a newer session. Only an exact
// JSON not-found response can establish the compatibility fallback.
export async function readLandingProfile(token, signal) {
  if (typeof token !== "string" || !token) throw new Error("A sign-in token is required.");
  const response = await fetch("/api/skills-workspace", {
    method: "GET",
    cache: "no-store",
    redirect: "error",
    signal,
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
  });
  if (!/^application\/json(?:\s*;.*)?$/i.test(response.headers.get("content-type") ?? ""))
    throw new Error("The host profile response is not JSON.");
  const value = await response.json();
  if (response.status === 200) return confirmedSkillWorkspaceProfile(value);
  if (
    response.status === 404 &&
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === 1 &&
    Object.hasOwn(value, "error") &&
    typeof value.error === "string" &&
    value.error.trim().length > 0
  )
    throw new ApiError(404, {}, "Host profile is not supported.");
  throw new Error("The authenticated host profile could not be confirmed.");
}

// One mounted landing attempt. Retirement/deadline prevents late navigation even
// when a transport ignores AbortSignal. A retry is a new read, never a mutation.
export function startHostLanding({ readProfile, getToken, navigate, onError, timeoutMs = 10000 }) {
  const token = getToken();
  if (!token) {
    onError("Sign in again to choose the home page.");
    return () => undefined;
  }
  const controller = new AbortController();
  let active = true;
  let timer;
  const finish = (callback) => {
    if (!active) return;
    active = false;
    clearTimeout(timer);
    if (getToken() !== token) {
      controller.abort();
      onError("Sign-in changed while checking the host. Retry with the current session.");
      return;
    }
    callback();
  };
  timer = setTimeout(() => {
    finish(() => {
      controller.abort();
      onError("The host check timed out. Retry to choose the home page.");
    });
  }, timeoutMs);
  const failed = (error) => {
    finish(() => {
      if (
        error instanceof ApiError &&
        error.status === 404 &&
        !error.pending &&
        !error.unknown &&
        error.retry !== false
      ) {
        navigate(DEFAULT_HOME);
      } else {
        onError("Cannot confirm this host's home page. Retry, or sign in again.");
      }
    });
  };
  try {
    Promise.resolve(readProfile(token, controller.signal)).then((profile) => {
      try {
        confirmedSkillWorkspaceProfile(profile);
        finish(() => navigate(SKILLS_HOME));
      } catch (error) {
        failed(error);
      }
    }, failed);
  } catch (error) {
    failed(error);
  }
  return () => {
    active = false;
    clearTimeout(timer);
    controller.abort();
  };
}
