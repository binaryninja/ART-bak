import { redirect } from "next/navigation";

import { HostLanding } from "@/components/host-landing";
import { signInLanding } from "@/lib/host-landing.mjs";
import { MOCK } from "@/lib/mock/enabled";

export default function Home() {
  const destination = signInLanding(process.env.NEXT_PUBLIC_ARTEX_BACKEND_PROFILE, MOCK);
  if (destination !== "/") redirect(destination);
  return <HostLanding />;
}
