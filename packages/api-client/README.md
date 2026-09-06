# `@clipfarm/api-client`

The ClipFarm api client — every request the product makes to `api/`, plus the
pure helpers that go with it (error-message formatting, auth error codes, the
progress ETA estimator, and the games cache).

Shared because the Expo app makes the same calls as the web app (CF-313). It is
platform-agnostic: no `next/*`, no `@supabase/ssr`, no `process.env`, no DOM
API. `fetch` is the only web-standard global it uses, and React Native has it.

## Using it

The client is inert until a host binds it. Do that once, at app start, before
anything issues a request:

```ts
import { configureApiClient } from "@clipfarm/api-client";

configureApiClient({
  baseUrl: "https://api.clipfarm.ca",
  getToken: async () => (await SecureStore.getItemAsync("access_token")) ?? null,
});
```

- **`baseUrl`** — root of the api, no trailing slash. Paths are appended verbatim.
- **`getToken`** — the caller's bearer token, or `null` when signed out. Called
  before every request, so a token that refreshes mid-session is picked up
  without re-configuring. May be async, and may reject: a token that cannot be
  read sends the request out unauthenticated rather than failing it.

Then import what you need from the package root:

```ts
import { getGames, type Game } from "@clipfarm/api-client";
```

`web/src/lib/api.ts` is the web binding — it configures the client from
`NEXT_PUBLIC_API_URL` and the Supabase browser session and re-exports the
package, so app code there imports `@/lib/api` as it always has.

## Shape of the package

It ships TypeScript source rather than a compiled `dist/`, and its `exports`
point at `src/index.ts`. Both consumers bundle it themselves — Next through
`transpilePackages`, Metro through `babel-preset-expo` — so a build step would
add an ordering dependency (and a chance of a stale artifact) in exchange for
nothing either one can use. `npm run typecheck` is what proves it compiles;
`npm test` runs its suite.

`tsconfig.json` includes the `DOM` lib for the *types* of `fetch`, `Response`,
`FormData` and `File`. That is a type-level borrow of web-standard names, not a
runtime dependency on a browser; Expo's `tsconfig.base` includes the same lib.
