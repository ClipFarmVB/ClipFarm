"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { AtSign } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/contexts/AuthContext";
import { useMe } from "@/lib/useMe";

/**
 * Prompts a signed-in user who has no handle yet to claim one (CF-107).
 *
 * A prompt rather than a forced redirect: a redirect gate on every route is
 * easy to get wrong (loops, bouncing people out of an upload mid-flight) for a
 * profile that nothing depends on yet. The hard requirement — no posting
 * without a handle — belongs with posting itself (CF-109).
 */
export function ClaimHandleBanner() {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  // Shares the sidebar's cached /users/me rather than issuing its own request.
  // `me` is null while signed out, still loading, or if the lookup failed —
  // all cases where staying quiet beats showing a prompt we can't substantiate.
  const me = useMe(Boolean(user) && !loading);
  const needsHandle = me !== null && !me.username;

  // Don't nag on the page that resolves it.
  if (!needsHandle || pathname?.startsWith("/settings/profile")) return null;

  return (
    <div className="mb-6 flex items-center gap-3 rounded-md border border-brand/30 bg-brand/10 px-4 py-3">
      <AtSign className="h-4 w-4 shrink-0 text-brand" />
      <div className="flex-1 text-sm">
        <span className="font-medium">Pick a username</span>
        <span className="text-muted"> — it&apos;s how teammates find you.</span>
      </div>
      <Link href="/settings/profile">
        <Button size="sm">Choose</Button>
      </Link>
    </div>
  );
}
