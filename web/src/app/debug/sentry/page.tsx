import { notFound } from "next/navigation";

import ThrowErrorButton from "./ThrowErrorButton";

// CF-89 acceptance helper: throws a client-side error to confirm it reaches
// Sentry with a stack trace.
//
// Gated to non-production so a public deploy doesn't expose a "throw an error"
// button (and so stray hits can't spam the issue feed / alert channel). This
// mirrors the api + worker debug hooks, which register only when settings.debug
// is on. A production build 404s this route.
export default function DebugSentryPage() {
  if (process.env.NODE_ENV === "production") {
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
