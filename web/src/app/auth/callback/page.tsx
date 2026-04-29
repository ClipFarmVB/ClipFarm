"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";

export default function AuthCallbackPage() {
  const router = useRouter();

  useEffect(() => {
    const supabase = createClient();

    supabase.auth.onAuthStateChange((event: string) => {
      if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED") {
        router.replace("/games");
      }
    });

    supabase.auth.getSession().then(({ data }: { data: { session: unknown } }) => {
      if (data.session) router.replace("/games");
    });
  }, [router]);

  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <div className="text-center">
        <div className="mx-auto mb-3 h-6 w-6 rounded-full border-2 border-border-strong border-t-brand animate-spin" />
        <p className="text-[13px] text-muted">Signing you in…</p>
      </div>
    </div>
  );
}
