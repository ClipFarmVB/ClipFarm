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
  // Keyed on the handle so a client navigation between two profiles REMOUNTS
  // rather than reusing the instance. Without it the effect re-runs but the
  // state does not reset, so a failed `/u/alcie` left `error` truthy and
  // `/u/bob` rendered the failure branch carrying alice's message — and
  // because that branch is `error || !profile`, the successful fetch could
  // not clear it. Resetting inside the effect is the other way to fix it and
  // is what `react-hooks/set-state-in-effect` exists to refuse (CF-304).
  return <ProfileView key={handle} handle={handle} />;
}
