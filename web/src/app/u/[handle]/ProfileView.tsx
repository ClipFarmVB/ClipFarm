"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, Lock, Pencil } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/contexts/AuthContext";
import { getProfile, type Profile } from "@/lib/api";
import { useMe } from "@/lib/useMe";

/**
 * Public profile body at /u/{handle} (CF-107).
 *
 * Identity only for now: the clip grid arrives with posts (CF-109) and is gated
 * by the visibility model (CF-108). A private account still renders its profile
 * so someone can find it and request to follow — only the *content* is hidden.
 *
 * Split out of page.tsx so the SOCIAL_ENABLED check can live in a server
 * component: `notFound()` from a client component only runs after hydration,
 * which flashes the profile before replacing it with the 404.
 */
export function ProfileView({ handle }: { handle: string }) {
  const { user, loading: authLoading } = useAuth();
  const me = useMe(Boolean(user) && !authLoading);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Compared by id rather than handle so a rename can't briefly hide the
  // owner's own edit affordance.
  const isSelf = Boolean(me && profile && me.id === profile.id);

  useEffect(() => {
    let cancelled = false;
    getProfile(handle)
      .then((data) => {
        if (!cancelled) setProfile(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [handle]);

  if (loading) return <div className="text-sm text-muted">Loading…</div>;

  if (error || !profile) {
    return (
      <div className="flex flex-col items-center py-16 text-center">
        <AlertCircle className="h-8 w-8 text-muted" />
        <h1 className="mt-3 text-lg font-medium">Profile not found</h1>
        <p className="mt-1 text-sm text-muted">
          No one is using @{handle}.
        </p>
        <Link href="/games" className="mt-4">
          <Button variant="secondary" size="sm">Back to library</Button>
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-start gap-5">
        {/* eslint-disable-next-line @next/next/no-img-element -- see settings/profile */}
        <img
          src={profile.avatar_url ?? "/favicon.ico"}
          alt=""
          className="h-20 w-20 rounded-full border border-border object-cover"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight">
              {profile.display_name || `@${profile.username}`}
            </h1>
            {profile.is_private && (
              <span
                className="flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs text-muted"
                title="Private account"
              >
                <Lock className="h-3 w-3" /> Private
              </span>
            )}
          </div>
          <p className="text-sm text-muted">@{profile.username}</p>
          {profile.bio && <p className="mt-2 whitespace-pre-line text-sm">{profile.bio}</p>}
        </div>
        {isSelf && (
          <Link href="/settings/profile">
            <Button variant="secondary" size="sm">
              <Pencil className="h-3.5 w-3.5" />
              Edit profile
            </Button>
          </Link>
        )}
      </div>

      <div className="mt-8 rounded-md border border-dashed border-border px-4 py-10 text-center">
        <p className="text-sm text-muted">
          {profile.is_private && !isSelf
            ? "This account is private. Follow to see their clips."
            : "No posts yet."}
        </p>
      </div>
    </div>
  );
}
