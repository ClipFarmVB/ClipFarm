import { notFound } from "next/navigation";

import ThrowErrorButton from "./ThrowErrorButton";

// CF-89 acceptance helper: throws a client-side error to confirm it reaches
// Sentry with a stack trace.
//
// Enabled when either:
//   - this is a dev build (NODE_ENV !== "production"), so `next dev` and the
//     docker compose stack work with no configuration, or
//   - NEXT_PUBLIC_ENABLE_DEBUG_ROUTES=true is set explicitly, which is how a
//     staging/preview deploy opts in to verifying Sentry end-to-end.
//
// Otherwise the route 404s, so a public prod deploy doesn't expose a "throw an
// error" button and stray hits can't spam the issue feed or the alert channel.
// The flag is opt-in (absent = disabled), so forgetting to set it fails closed.
//
// Note this resolves at BUILD time, not per request: the route has no dynamic
// data so Next prerenders it, and NEXT_PUBLIC_* is inlined into the bundle.
// Flipping it therefore needs a rebuild — unlike the api/worker debug hooks,
// which read settings.debug at runtime.
const debugRoutesEnabled =
  process.env.NODE_ENV !== "production" ||
  process.env.NEXT_PUBLIC_ENABLE_DEBUG_ROUTES === "true";

export default function DebugSentryPage() {
  if (!debugRoutesEnabled) {
    notFound();
  }

  return (
    <main style={{ padding: 24 }}>
      <h1>Sentry debug</h1>
      <p>Clicking the button throws a test error captured by Sentry.</p>
      <ThrowErrorButton />
    </main>
  );
}
