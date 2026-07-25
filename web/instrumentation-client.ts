import * as Sentry from "@sentry/nextjs";

// Browser Sentry init (CF-89). The DSN must be NEXT_PUBLIC_* to reach the
// client bundle. No-op when unset so local dev needs no Sentry account.
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment:
      process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? process.env.NODE_ENV,
    tracesSampleRate: Number(
      process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? 0,
    ),
    // Ties each event to a commit and matches it to the uploaded source maps,
    // so prod stack traces resolve to real files instead of minified bundles.
    release: process.env.NEXT_PUBLIC_SENTRY_RELEASE || undefined,
    sendDefaultPii: false,
  });
}

// Instruments client-side navigations for tracing.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
