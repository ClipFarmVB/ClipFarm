"use client";

// CF-89 acceptance helper: throws a client-side error to confirm it reaches
// Sentry with a stack trace. Safe to leave in — it does nothing until clicked.
export default function DebugSentryPage() {
  return (
    <main style={{ padding: 24 }}>
      <h1>Sentry debug</h1>
      <p>Clicking the button throws a test error captured by Sentry.</p>
      <button
        type="button"
        onClick={() => {
          throw new Error("CF-89 test error from the web app (debug only)");
        }}
      >
        Throw test error
      </button>
    </main>
  );
}
