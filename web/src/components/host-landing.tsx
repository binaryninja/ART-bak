"use client";

import { useLayoutEffect, useState } from "react";

import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { auth } from "@/lib/auth";
import { readLandingProfile, startHostLanding } from "@/lib/host-landing.mjs";

export function HostLanding() {
  const router = useRouter();
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState("");

  // biome-ignore lint/correctness/useExhaustiveDependencies: attempt explicitly starts a fresh read after a manual retry.
  useLayoutEffect(() => {
    setError("");
    if (!auth.getToken()) {
      // A leftover cookie alone must not bounce between the proxy and login.
      auth.clearToken();
      window.location.replace("/login");
      return;
    }
    return startHostLanding({
      readProfile: readLandingProfile,
      getToken: () => auth.getToken(),
      navigate: (destination) => router.replace(destination),
      onError: setError,
    });
  }, [attempt, router]);

  function signInAgain() {
    auth.clearToken();
    window.location.replace("/login");
  }

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <div className="max-w-md space-y-4 text-center">
        <h1 className="font-semibold text-xl">Opening your workspace</h1>
        {error ? (
          <>
            <p role="alert" className="text-destructive text-sm">
              {error}
            </p>
            <div className="flex justify-center gap-2">
              <Button onClick={() => setAttempt((value) => value + 1)}>Retry host check</Button>
              <Button variant="outline" onClick={signInAgain}>
                Sign in again
              </Button>
            </div>
          </>
        ) : (
          <p role="status" className="text-muted-foreground text-sm">
            Checking the authenticated host…
          </p>
        )}
      </div>
    </main>
  );
}
