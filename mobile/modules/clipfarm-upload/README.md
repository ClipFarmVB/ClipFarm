# clipfarm-upload

The native background-upload module. **It has no native code yet** — CF-323
(#373) adds the Swift half and CF-324 (#374) the Kotlin half. This directory,
its config plugin and the JS contract in `mobile/src/upload/` exist so that
neither ticket has to invent an interface the other then has to match.

## What is already decided

- **The JS contract** is `mobile/src/upload/types.ts`. Read the five rules at
  the top of that file before writing either native half; they are the parts
  the two implementations have to agree on and that the type signatures do not
  say.
- **The native module name is `ClipFarmUpload`** and it emits one event,
  `onUploadChange`, carrying a whole `UploadTask`. `mobile/src/upload/index.ts`
  looks for exactly that and falls back to the JS mock when it is absent, so
  the app keeps running — and CF-330 keeps building — until you land.

## Adding a platform

1. Create `ios/` (or `android/`) in **this** directory and write the module
   there. It is committed: `mobile/.gitignore` ignores `/ios` and `/android`
   with a leading slash, so it catches the generated projects at the app root
   and not a module's own native sources.
2. Add the platform to `expo-module.config.json` — `"apple"` with
   `"modules": ["ClipFarmUploadModule"]`, or `"android"` with its Kotlin class.
   Autolinking picks it up from `mobile/modules/` with no further wiring.
3. Put anything the generated project needs — entitlements, permissions,
   service declarations — in `app.plugin.js`, never in a generated `ios/` or
   `android/` file. Those directories are build output; `expo prebuild`
   rewrites them and a hand edit disappears with the next build.
4. `npx expo prebuild --clean` then `npx expo run:ios` / `run:android` to
   exercise it. `npm run prebuild --workspace=mobile` does the same.

## Why the native directories are not in git

Two native modules and two teams is exactly the setup where a generated `ios/`
gets committed "just to unblock the build", after which every prebuild is a
merge conflict and the config plugins stop being the source of truth. Keeping
them ignored from the start (CF-315) is cheaper than untangling that later.
