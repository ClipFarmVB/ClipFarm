import { Redirect } from "expo-router";

import { useSession } from "@/lib/session";

/**
 * Entry point. The library is home for a signed-in filmer; everyone else signs
 * in first. A deep link (CF-326) lands on its own route and never comes
 * through here.
 */
export default function Index() {
  const { session } = useSession();
  return <Redirect href={session ? "/games" : "/login"} />;
}
