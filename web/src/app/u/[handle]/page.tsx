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
  return <ProfileView handle={handle} />;
}
