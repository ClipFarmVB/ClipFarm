"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, Lock, Pencil, UserX } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/contexts/AuthContext";
import { getProfile, type Profile } from "@/lib/api";
// From `apiError`, not `api`: the class must be the real one even where a
// test replaces the client module wholesale.
import { ApiError } from "@/lib/apiError";
import { useMe } from "@/lib/useMe";
import { PostGrid } from "./PostGrid";

/**
 * Public profile body at /u/{handle} (CF-107).
 *
 * Identity plus the post grid (CF-109), gated by the visibility model (CF-108).
 * A private account still renders its profile so someone can find it and
 * request to follow, AND still renders its grid: `is_private` governs follow
 * approval and nothing else, so the filtering is `GET /posts?username=`'s job
 * and it does it in SQL for the asking viewer. The private-account notice is a
 * notice; it stopped standing in for the grid because doing so told a stranger
 * the content was gone while the API went on serving it.
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
  const [notFound, setNotFound] = useState(false);

  // Compared by id rather than handle so a rename can't briefly hide the
  // owner's own edit affordance.
  const isSelf = Boolean(me && profile && me.id === profile.id);

  useEffect(() => {
    let cancelled = false;
    // No resets here on purpose. This effect re-runs on a handle change, and
    // the state it would have to clear is `profile`, `error` and `loading` —
    // but `react-hooks/set-state-in-effect` rejects that, and rightly: an
    // instance should never outlive the handle it was fetched for. The Next
    // router already remounts this subtree when the `[handle]` param changes,
    // and `page.tsx` also gives this component `key={handle}` so that holds
    // independent of router internals. Either way there is no stale state to
    // clear.
    getProfile(handle)
      .then((data) => {
        if (!cancelled) setProfile(data);
      })
      .catch((e) => {
        // `instanceof`, never a bare `e.message`: a NULLISH rejection makes
        // `e.message` throw inside this catch, so the chain rejects with
        // nothing left to handle it.
        //
        // Only an `ApiError` with status 404 is "not found" (CF-423). A 5xx, a
        // 429, a dropped connection (a `TypeError` from fetch) and a non-Error
        // rejection all say nothing about whether the profile exists, so they
        // stay in the fault arm below.
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) setNotFound(true);
        else setError(e instanceof Error ? e.message : "Please try again.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [handle]);

  if (loading) return <div className="text-sm text-muted">Loading…</div>;

  // A 404 gets its own arm, and only a 404 does (CF-423).
  //
  // `getProfile` is `Promise<Profile>` through `request()`, so a missing handle
  // REJECTS, with an `ApiError` whose status is 404 (`_by_handle` and the
  // `username_is_generated` check in `profiles.py`). That is the common failure
  // — a mistyped handle — and until the status reached the client it shared
  // one arm with a server fault.
  //
  // The copy must still not say the handle is free. The API answers 404 for a
  // handle nobody holds AND for an account whose handle was generated
  // (deliberately, so the route cannot enumerate accounts), so "no profile
  // here" is true and "no one is using this" may not be. Rendering that for any
  // failure is what CF-304 removed; this arm must not bring it back for a 404.
  if (notFound) {
    return (
      <div className="flex flex-col items-center py-16 text-center">
        <UserX className="h-8 w-8 text-muted" />
        <h1 className="mt-3 text-lg font-medium">No profile at @{handle}</h1>
        <p className="mt-1 text-sm text-muted">Check the link or the spelling of the handle.</p>
        <Link href="/games" className="mt-4">
          <Button variant="secondary" size="sm">Back to library</Button>
        </Link>
      </div>
    );
  }

  // Everything else — a server fault, a rate limit, a dropped connection — is
  // no evidence about the handle, so this arm asserts nothing about it and
  // shows what went wrong.
  //
  // `!profile` stays in this condition rather than getting its own arm.
  // `request()` has one resolved-undefined path — a 204, or any response with
  // `content-length: 0` — which this endpoint does not produce but which the
  // types do not rule out, and TypeScript needs the narrowing regardless. A
  // profile that arrived empty is not evidence the handle is free either.
  if (error || !profile) {
    return (
      <div className="flex flex-col items-center py-16 text-center">
        <AlertCircle className="h-8 w-8 text-muted" />
        <h1 className="mt-3 text-lg font-medium">Couldn&apos;t load @{handle}</h1>
        <p className="mt-1 text-sm text-muted">{error ?? "Please try again."}</p>
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

      {profile.is_private && !isSelf && (
        // A notice, no longer a REPLACEMENT for the grid.
        //
        // This used to render instead of the grid, which made the web say the
        // opposite of both the API and the settings screen: `is_private`
        // governs whether following needs approval and nothing else, so a
        // private account's `public` post is deliberately readable by anyone —
        // `services/access.py` argues that at length and
        // `test_account_privacy_does_not_clamp_post_visibility` pins it.
        //
        // Hiding the grid therefore reassured a stranger that the content was
        // gone while `GET /posts?username=` went on serving it, which is the
        // worse of the two ways to be wrong. The comment that used to sit in
        // the other arm of this branch said exactly that, from inside the
        // branch doing it.
        //
        // Nothing leaks by showing the grid: `getUserPosts` filters by
        // visibility in SQL for the asking viewer, so a stranger sees the
        // public posts and no others — today, none.
        // And the notice itself has to be true. "Follow to see clips shared
        // with followers" told a stranger to do something that does not exist:
        // there is no follows table and no follows router, `is_follower`
        // returns False unconditionally until CF-110, and that sentence was the
        // only "Follow" call to action anywhere in `web/src`. It also restated
        // the model this PR exists to correct, on the page directly above the
        // grid — while the same commit told the settings screen to say
        // "Following isn't built yet". One standard, both surfaces.
        <div className="mt-8 rounded-md border border-dashed border-border px-4 py-3 text-center">
          <p className="text-sm text-muted">
            This account is private. That controls who can follow it, not who
            can see its clips &mdash; and following isn&apos;t built yet, so it
            has no effect today. Anything shown below is public.
          </p>
        </div>
      )}
      <PostGrid handle={handle} isSelf={isSelf} />
    </div>
  );
}
