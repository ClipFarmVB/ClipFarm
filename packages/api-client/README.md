# `@clipfarm/api-client`

The ClipFarm api client — every request the product makes to `api/`, plus the
pure helpers that go with it (error-message formatting, auth error codes, the
progress ETA estimator, and the games cache).

Shared so the planned Expo app can make the same calls as the web app (CF-313).
It imports no `next/*`, no `@supabase/ssr` and no `process.env`, and touches no
`window`, `document` or storage. It does call three web-standard globals, listed
exactly under [What it needs from the host](#what-it-needs-from-the-host).

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
package, so app code there imports `@/lib/api` as it always has. The one
exception is `app/auth/confirm/route.ts`, a server route that sends no request:
it takes its auth constants from the package directly, so it does not load the
browser session.

## Shape of the package

It ships TypeScript source rather than a compiled `dist/`, and its `exports`
point at `src/index.ts`. Both consumers bundle it themselves — Next through
`transpilePackages`, Metro through `babel-preset-expo` — so a build step would
add an ordering dependency (and a chance of a stale artifact) in exchange for
nothing either one can use. `npm run typecheck` is what proves it compiles,
`npm run lint` lints it, and `npm test` runs its suite — all three are CI steps.

## What it needs from the host

`tsconfig.json` includes the `DOM` lib, because that is where TypeScript
declares `fetch` and friends. React Native implements only part of that lib, so
the lib is not the contract. This is — every `lib.dom` name the package uses:

| Name | Used for | At runtime? |
|---|---|---|
| `fetch` | every request | yes |
| `Response` `.ok` `.status` `.headers`, `.json()` `.text()`; `Headers.get` | reading what `fetch` returns | yes, on the object `fetch` returns |
| `RequestInit` `.headers` | the options passed to `fetch` | type, plus the one field read |
| `URLSearchParams` — constructor, `.append`, `.toString` | query strings (`getClips`, `getUserPosts`) | yes |
| `FormData` — constructor, `.append` | the avatar upload | yes |
| `File` | the avatar upload's parameter type | type only |

React Native provides all of these. Its bundled `URLSearchParams` implements
little beyond `append` and `toString` — `set`, `get`, `has`, `delete` and
`sort` throw "not implemented" — which is why `getClips` appends. That is from
RN's polyfill, not from a run against an Expo app (none exists yet); CF-315
should confirm it on the RN version it lands on.

`src/platformSurface.test.ts` holds the package to this table: it asks the type
checker which `lib.dom` declarations the source resolves to and fails if the
set differs from its list, in either direction. Adding a name means adding it
there, which is the moment to check React Native has it, and then here.

The tsconfig also sets `"types": []`, which is what makes the typecheck mean anything: left
unset, tsc auto-includes whatever `@types/*` the installer hoisted to the repo
root — `node`, `react`, `react-dom` from `web` — and the package would compile
against ambient `process`, `Buffer` and `setImmediate` that no React Native
consumer has.
