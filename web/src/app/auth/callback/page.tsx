"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { createClient } from "@/lib/supabase";

/** How long to wait for Supabase to turn the link into a session before giving up. */
const EXCHANGE_TIMEOUT_MS = 10_000;

/**
 * Only same-origin paths are honoured, so a crafted `next` cannot bounce a
 * freshly signed-in user off to another site.
 */
function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//")) return "/games";
  return next;
}

function AuthCallback() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = safeNext(searchParams.get("next"));

  // Supabase bounces failures (expired or already-used links) back here as
  // query params rather than a code.
  const linkError = searchParams.get("error")
    ? (searchParams.get("error_description") ?? searchParams.get("error"))
    : null;
  const [timedOut, setTimedOut] = useState(false);
  const error = linkError ?? (timedOut ? "That link has expired or was already used." : null);

  useEffect(() => {
    if (linkError) return;

    const supabase = createClient();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event: string) => {
      if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED") {
        router.replace(next);
      }
    });

    supabase.auth.getSession().then(({ data }: { data: { session: unknown } }) => {
      if (data.session) router.replace(next);
    });

    // The client exchanges the link for a session on its own; if that never
    // lands, say so rather than spinning forever.
    const timer = setTimeout(() => setTimedOut(true), EXCHANGE_TIMEOUT_MS);

    return () => {
      clearTimeout(timer);
      subscription.unsubscribe();
    };
  }, [router, next, linkError]);

  if (error) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center fade-up">
        <div className="w-full max-w-[340px] text-center">
          <div className="mx-auto mb-4 flex h-10 w-10 items-center justify-center rounded-full bg-red-500/10 border border-red-500/20">
            <AlertCircle size={18} className="text-red-400" />
          </div>
          <h1 className="text-[16px] font-semibold text-foreground">Couldn&apos;t sign you in</h1>
          <p className="mt-2 text-[13px] text-muted leading-relaxed">{error}</p>
          <Link href="/login">
            <Button variant="secondary" size="sm" className="mt-5">
              Back to login
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <div className="text-center">
        <div className="mx-auto mb-3 h-6 w-6 rounded-full border-2 border-border-strong border-t-brand animate-spin" />
        <p className="text-[13px] text-muted">Signing you in…</p>
      </div>
    </div>
  );
}

export default function AuthCallbackPage() {
  return (
    <Suspense fallback={null}>
      <AuthCallback />
    </Suspense>
  );
}
