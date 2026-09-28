"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { DEFAULT_NEXT } from "@/lib/redirect";

/**
 * Backstop for the middleware's signed-in `/` → /games redirect.
 *
 * `/` is static, so the client router can serve a prefetched copy without a
 * request ever reaching the middleware — a signed-in user following a cached
 * link would see the landing page. The Sidebar's brand links already point
 * signed-in users at /games; this catches whatever other path gets them here.
 */
export function SignedInRedirect() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (user && !loading) router.replace(DEFAULT_NEXT);
  }, [user, loading, router]);

  return null;
}
