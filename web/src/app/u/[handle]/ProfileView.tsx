"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, Lock, Pencil } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/contexts/AuthContext";
import { getProfile, type Profile } from "@/lib/api";
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

  // Compared by id rather than handle so a rename can't briefly hide the
  // owner's own edit affordance.
  const isSelf = Boolean(me && profile && me.id === profile.id);

  useEffect(() => {
    let cancelled = false;
    // No resets here on purpose. This effect re-runs on a handle change, and
    // the state it would have to clear is `profile`, `error` and `loading` —
    // but `react-hooks/set-state-in-effect` rejects that, and rightly: the
    // real problem is that one instance was outliving the handle it was
    // fetched for. `page.tsx` gives this component `key={handle}`, so a client
    // navigation remounts it and there is no stale state to clear.
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

  // **A failure and a genuine absence are the same branch here, because the
  // client cannot tell them apart — and the first attempt at this made things
  // worse by pretending it could.**
  //
  // `getProfile` is `Promise<Profile>` through `request()`, and a missing
  // handle is a 404 (`profiles.py:448`, and `:170` in `_by_handle`), so it
  // REJECTS. A separate `if (!profile)` branch below an `if (error)` one
  // therefore cannot be the not-found case, and splitting them sent the COMMON
  // failure — a mistyped handle — to the copy written for a server fault.
  //
  // `!profile` stays in this condition rather than getting its own arm.
  // `request()` does have one resolved-undefined path — a 204, or any response
  // with `content-length: 0` (`api.ts:76`) — which this endpoint does not
  // produce but which the types do not rule out, and TypeScript needs the
  // narrowing regardless. It shares the arm because the copy below is right for
  // it too: a profile that arrived empty is not evidence the handle is free.
  //
  // What this must not do is what it used to: render "No one is using @handle"
  // for any failure, so that a 500 or a dropped connection asserted the
  // availability of a handle that may well be taken (CF-304).
  //
  // So it asserts nothing about the handle and shows the server's own words.
  // `apiErrorMessage` returns `detail` verbatim, which is "Profile not found"
  // for the 404 — accurate for the common case without the client having to
  // infer a status it is never given. Distinguishing properly needs an error
  // type that carries one, which is a change to every caller's error shape:
  // CF-423 (#556), not smuggled in here.
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
