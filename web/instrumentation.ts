import * as Sentry from "@sentry/nextjs";

// Server + edge runtime Sentry init (CF-89). No-op without a DSN.
export async function register() {
  const dsn = process.env.SENTRY_DSN ?? process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;

  if (
    process.env.NEXT_RUNTIME === "nodejs" ||
    process.env.NEXT_RUNTIME === "edge"
  ) {
    Sentry.init({
      dsn,
      environment: process.env.SENTRY_ENVIRONMENT ?? process.env.NODE_ENV,
      tracesSampleRate: Number(process.env.SENTRY_TRACES_SAMPLE_RATE ?? 0),
      // Same release identifier as the browser side, so server and client
      // events for one deploy group together.
      release:
        process.env.SENTRY_RELEASE ||
        process.env.NEXT_PUBLIC_SENTRY_RELEASE ||
        undefined,
      sendDefaultPii: false,
    });
  }
}

// Captures errors thrown in nested React Server Components (Next 15+/16).
export const onRequestError = Sentry.captureRequestError;
