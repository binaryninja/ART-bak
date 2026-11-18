"use client";
import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
function Redirect() { const router=useRouter(); const id=useSearchParams().get("id"); useEffect(() => { router.replace(id ? `/function/tasks/detail?id=${encodeURIComponent(id)}` : "/function/assessments"); },[id,router]); return <p className="p-6">Opening assessment…</p>; }
export default function ReconPage() { return <Suspense fallback={null}><Redirect /></Suspense>; }
