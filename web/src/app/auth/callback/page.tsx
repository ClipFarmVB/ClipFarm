"use client";

import { Suspense, useEffect, useState, useSyncExternalStore } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { createClient } from "@/lib/supabase";

/** How long to wait for Supabase to turn the link into a session before giving up. */
const EXCHANGE_TIMEOUT_MS = 10_000;

/**
 * Where a confirmed link lands. Hardcoded rather than taken from the URL: a
 * caller-supplied destination is an open-redirect surface, and it would have to
 * ride along in `emailRedirectTo`, which Supabase glob-matches in full against
 * its Redirect URLs allowlist.
 */
const DESTINATION = "/games";

function subscribeToHash(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

/** First source that carries an `error` wins; its description is the useful part. */
function readError(...sources: { get(name: string): string | null }[]): string | null {
  for (const source of sources) {
    const error = source.get("error");
    if (error) return source.get("error_description") ?? error;
  }
  return null;
}

function AuthCallback() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // GoTrue reports a bad link in the query string on PKCE links and in the
  // fragment on the older ones, so both have to be read. The fragment never
  // reaches the server, hence the external store rather than plain state.
  const hash = useSyncExternalStore(
    subscribeToHash,
    () => window.location.hash,
    () => ""
  );
  const linkError = readError(searchParams, new URLSearchParams(hash.replace(/^#/, "")));

  const [timedOut, setTimedOut] = useState(false);
  const error = linkError ?? (timedOut ? "That link has expired or was already used." : null);

  useEffect(() => {
    if (linkError) return;

    const supabase = createClient();

    // The client exchanges the link for a session on its own; if that never
    // lands, say so rather than spinning forever.
    const timer = setTimeout(() => setTimedOut(true), EXCHANGE_TIMEOUT_MS);

    // Stop the clock as the redirect starts: the transition to a guarded route
    // can outlast the timeout on a cold instance, and an error over a session
    // that worked is worse than a slow spinner.
    function leave() {
      clearTimeout(timer);
      router.replace(DESTINATION);
    }

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event: string) => {
      if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED") leave();
    });

    supabase.auth.getSession().then(({ data }: { data: { session: unknown } }) => {
      if (data.session) leave();
    });

    return () => {
      clearTimeout(timer);
      subscription.unsubscribe();
    };
  }, [router, linkError]);

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
