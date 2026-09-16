# ClipFarm mobile

The iOS and Android app (CF-313), built with Expo over the same API and the
same Supabase project the web app uses — an existing ClipFarm account signs in
here with no migration.

## Running it

```bash
npm ci                                    # at the repo root, once per clone
cp mobile/.env.example mobile/.env        # then fill in the Supabase values
npm run start --workspace=mobile
```

Expo Go is enough for the placeholder screens and the session. It is **not**
enough once the background-upload modules land (CF-323, CF-324): custom native
code needs a development build.

The sign-in screen is the one placeholder that works: an existing ClipFarm
account signs in, and the session is read back from the keychain on the next
cold start. That is the scaffold proving its own wiring — CF-328 replaces the
screen with the real one.

```bash
npx expo prebuild --clean                 # regenerates ios/ and android/
npx expo run:ios                          # or run:android
```

`ios/` and `android/` are **generated, and gitignored**. Everything they need
comes from `app.json` and the config plugins, so a hand edit inside either one
disappears at the next prebuild — put the change in a plugin instead. See
`modules/clipfarm-upload/README.md`.

## Builds

`eas.json` carries three profiles:

| Profile | What it produces |
|---|---|
| `development` | A development client — iOS simulator build, Android APK. What you want while working on native code. |
| `preview` | An internal-distribution build for testers. |
| `production` | A store build: Android app bundle, versioned remotely. |

```bash
npx eas build --profile development --platform ios
```

The first `eas build` needs an Expo account and `npx eas init`, which writes
`extra.eas.projectId` into `app.json`. That is left out of this commit on
purpose — the id belongs to whichever Expo organisation owns the app, and
CF-345/CF-346 set it up along with the store accounts.

CI does not run EAS builds. They are slow and metered, and nothing about a
scaffold PR needs one.

## What lives where

| Path | |
|---|---|
| `src/app/` | Routes. Every screen in the epic is registered as a placeholder — a screen ticket replaces its own file and touches nothing shared. |
| `src/lib/session.tsx` | The session: restore on cold start, sign in/up/out, the access token for the API client. |
| `src/lib/secureStore.ts` | Keychain storage for that session, chunked around the 2048-byte Android cap. |
| `src/lib/apiClient.ts` | Temporary stand-in for `packages/api-client` (CF-314) — same `{ baseUrl, getToken }` shape, so adopting the real package is an import change. |
| `src/upload/` | The background-upload contract (CF-323/CF-324 implement it) and the mock CF-330 builds against. |
| `src/player/` | The `ClipSource` contract, so CF-332's player survives the mezzanine proxy landing under it. |
| `modules/clipfarm-upload/` | The local Expo module and its config plugin. No native code yet. |

## Checks

```bash
npm run lint --workspace=mobile
npm run typecheck --workspace=mobile
npm run test --workspace=mobile
```

The tests cover the contracts, not the screens: they are written against
injected dependencies so they run in plain Node. Screen tests arrive with the
screens and will need a React Native-aware runner.
