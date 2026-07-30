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
    sendDefaultPii: false,
  });
}

// Instruments client-side navigations for tracing.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
