import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

// One canonical release drives all three consumers — the source-map upload
// below, the server SDK (instrumentation.ts) and the browser SDK
// (instrumentation-client.ts). Resolved with the same precedence the server
// uses, so setting either variable alone still lines everything up.
//
// `SENTRY_RELEASE` is the convention the api and worker already follow
// (api/app/config.py), so a build that sets only that one must still work here.
const sentryRelease =
  process.env.SENTRY_RELEASE || process.env.NEXT_PUBLIC_SENTRY_RELEASE || "";

const nextConfig: NextConfig = {
  // The shared api client (CF-314) ships TypeScript source rather than a
  // built dist/, so Next has to put it through the compiler like app code —
  // workspace packages resolve into node_modules, which is otherwise left
  // alone.
  transpilePackages: ["@clipfarm/api-client"],
  // Re-export the resolved release under the public name. Next only inlines
  // NEXT_PUBLIC_* into the browser bundle, so instrumentation-client.ts cannot
  // read SENTRY_RELEASE itself — without this, a build that set only
  // SENTRY_RELEASE would upload maps and tag server events correctly while
  // shipping client events with no release, leaving browser traces minified
  // with nothing to indicate why.
  env: {
    NEXT_PUBLIC_SENTRY_RELEASE: sentryRelease,
  },
};

// Wrap with Sentry (CF-89). Source-map upload only runs when SENTRY_AUTH_TOKEN
// + org/project are set (i.e. in CI/prod); local builds are unaffected.
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  silent: !process.env.CI,
  // Pin the release the source maps upload under to the SAME value the runtime
  // tags events with. Left to auto-detection the two can diverge — the plugin
  // infers from git, which isn't reliable in a container build — and then events
  // never join their maps, so prod stack traces stay minified even though the
  // upload "succeeded".
  release: sentryRelease ? { name: sentryRelease } : undefined,
  // Route browser events through this app's own origin instead of directly to
  // ingest.sentry.io, so ad blockers / privacy filters can't drop client-side
  // errors. Adds a rewrite at /monitoring that proxies to Sentry.
  tunnelRoute: "/monitoring",
});
