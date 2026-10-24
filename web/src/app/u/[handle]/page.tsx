import { notFound } from "next/navigation";

import { SOCIAL_ENABLED } from "@/lib/features";

import { ProfileView } from "./ProfileView";

/**
 * A server component purely so the flag check happens before anything renders.
 * `NEXT_PUBLIC_SOCIAL_ENABLED` is inlined at build time, so with social off this
 * route is a 404 with no profile markup ever sent — the client component would
 * instead paint and then replace itself once hydrated.
 */
export default async function ProfilePage({
  params,
}: {
  params: Promise<{ handle: string }>;
}) {
  if (!SOCIAL_ENABLED) notFound();

  // Next 16: route params are a Promise.
  const { handle } = await params;
  // Keyed on the handle as a belt-and-braces measure, not a fix for a live
  // bug. The installed Next router already remounts on a handle change: it
  // keys each segment's subtree by its cache key, and a dynamic segment's
  // key carries the param value (`handle|alice|d`), so `/u/alice` to `/u/bob`
  // gets a fresh `ProfileView`. The key makes that independent of router
  // internals. `ProfileView` does not reset its state when `handle` changes,
  // so it relies on one of the two; resetting inside the effect is what
  // `react-hooks/set-state-in-effect` exists to refuse (CF-304).
  return <ProfileView key={handle} handle={handle} />;
}
