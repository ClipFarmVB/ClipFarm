import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  /* config options here */
};

// Wrap with Sentry (CF-89). Source-map upload only runs when SENTRY_AUTH_TOKEN
// + org/project are set (i.e. in CI/prod); local builds are unaffected.
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  silent: !process.env.CI,
  // Route browser events through this app's own origin instead of directly to
  // ingest.sentry.io, so ad blockers / privacy filters can't drop client-side
  // errors. Adds a rewrite at /monitoring that proxies to Sentry.
  tunnelRoute: "/monitoring",
});
