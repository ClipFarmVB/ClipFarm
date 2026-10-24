# @clipfarm/ui-mobile

Five React Native primitives — `Button`, `Card`, `Badge`, `Screen`, `Text` —
built on `@clipfarm/tokens`. Every colour comes from the token package, and so
does every distance a screen can see, so **a screen** never writes a hex value
or a magic number. The primitives themselves still write a few raw numbers
inside their own padding (`Button`'s `paddingVertical: 6`, `Badge`'s
`paddingVertical: 2`), where the value is web's and falls between steps on the
shared scale.

## Where this lives, and why

CF-316 and CF-315 (the Expo scaffold) were built in parallel, and this landed
first. So the primitives sit in `packages/` rather than `mobile/`: they are
pure `react-native` with no dependency on routing, session or the API client,
which is what let them ship before the app existed.

Nothing here is installed by the root `npm install` — `packages/ui-mobile` is
deliberately **not** in the root `workspaces` array, because `react-native` is
a peer dependency and pulling it into the web-only install would be a large
dependency for no benefit. Wiring it up is three steps in `mobile/`:

1. Add `"packages/ui-mobile"` to the root `workspaces` array.
2. Add `"@clipfarm/ui-mobile": "*"` to `mobile/package.json` dependencies.
3. Point Metro at the monorepo root (`watchFolders`) so it resolves the
   workspace symlink and transpiles the TypeScript source, which is what both
   packages export.

Then wrap the app in `ThemeProvider` and use the components. Moving the
directory into `mobile/` at that point is a `git mv` and an import rewrite;
leaving it in `packages/` is also fine, and keeps it importable from anywhere.

Until step 1 happens these files are not type-checked by CI. They do compile
clean against `react-native@0.81` types — `tsconfig.json` here is set up for
exactly that, so `npx tsc -p packages/ui-mobile` works as soon as react-native
is installed.

## Theme

```tsx
import { ThemeProvider, Screen, Text } from "@clipfarm/ui-mobile";

<ThemeProvider>
  <Screen scroll>
    <Text variant="title">Games</Text>
  </Screen>
</ThemeProvider>
```

`ThemeProvider` follows the OS colour scheme and falls back to **dark**, the
same default web uses. Pass `name="light" | "dark"` to force one, and
`fontFamily` once the app has loaded Inter (see `fontFamilyName` in the token
package — it is not defaulted, because naming an unloaded face renders wrong
rather than falling back cleanly).

`useTheme()` returns `{ name, colors, fontFamily }` for the rare case a screen
needs a raw value.

---

## Button

```tsx
<Button label="Publish" onPress={publish} />
<Button label="Delete" variant="danger" size="sm" onPress={remove} />
<Button label="Saving…" loading fullWidth />
```

| Prop | Values | Default |
|---|---|---|
| `variant` | `primary`, `secondary`, `ghost`, `danger` | `primary` |
| `size` | `sm`, `md`, `lg` | `md` |
| `loading` | boolean | `false` |
| `disabled` | boolean | `false` |
| `fullWidth` | boolean | `false` |

Plus `style` (a `ViewStyle`, merged last so it wins) and everything `Pressable`
takes except `children`.

**States**

- **Default** — each variant's resting fill, border and label. `primary` is the
  brand fill with `onBrand` text; `secondary` is a bordered surface; `ghost` is
  transparent with muted text; `danger` is a red wash.
- **Pressed** — the colours web reaches on *hover*, plus a `scale(0.97)`,
  matching web's `active:scale-[0.97]`. A phone has no hover, so hover-state
  colours are press-state colours here. `ghost` additionally lifts its label
  from `muted` to `foreground`, as it does on web.
- **Disabled** — 35% opacity on the whole button (web's `disabled:opacity-35`)
  and presses blocked. Announced via `accessibilityState.disabled`.
- **Loading** — a spinner in the variant's label colour, over a label that is
  laid out but transparent, so the button keeps its width instead of collapsing
  mid-request. Implies disabled: a second tap while a request is in flight is
  the usual way to double-post something. Announced as `busy`.

`label` is a string, not children — it feeds `accessibilityLabel` and the
loading layout needs to measure it.

## Card

```tsx
<Card>
  <CardHeader><CardTitle>Recent games</CardTitle></CardHeader>
  …
</Card>

<Card flush>
  <Image … />
</Card>
```

| Prop | Values | Default |
|---|---|---|
| `flush` | boolean | `false` |

**States** — no interactive states; wrap it in a `Pressable` if the whole card
is a target. `flush` drops the padding and clips children to the corners, for a
card whose first child is edge-to-edge media (iOS does not clip to a rounded
parent unless the parent asks).

`CardHeader` is a spacing wrapper. `CardTitle` renders a `subheading` with the
header accessibility role so VoiceOver can jump card to card.

## Badge

```tsx
<Badge label="spike" />
<Badge label="Serve" action="serve" />
```

| Prop | Values | Default |
|---|---|---|
| `label` | string | required |
| `action` | an `ActionType`, or any string | falls back to `label` |

**States** — none; it is not interactive. Colours come from `actionColors` in
the token package (`spike`, `serve`, `dig`, `set`, `block`, `unknown`,
`removed`, `not_an_action`). An unrecognised action degrades to the grey
`unknown` chip rather than rendering unstyled. `not_an_action` displays as
"removed", as it does on web.

The values match web, but not by sharing the map: web's Badge keeps its own
`ACTION_STYLES` table of Tailwind class names and imports nothing from the
token package. `web/src/components/ui/Badge.tokens.test.ts` is what holds the
two together — it resolves each Tailwind class against `palette` and compares
it to the token entry, so moving one side alone fails web's test run. (The
stylesheet snapshot cannot cover this: `themeCss()` emits only `ThemeColors`.)

Unlike the other primitives, badge colours do **not** change with the theme,
which matches web. That is **not** because the hues work on either background.
They are dark-theme hues: against the 10px label, five of the eight chips pass
AA on `#0c0c0e` and fail on `#ffffff`, and `removed` / `not_an_action` fail on
both. Web lands on dark unless the user flips `cf-theme` by hand, so it rarely
shows there; `ThemeProvider` here follows `useColorScheme()`, so a phone in
light mode meets it on first launch. Measured table and the two options
(theme `actionColors`, or keep one palette) are in `src/Badge.tsx`.

## Screen

```tsx
<Screen scroll>…</Screen>
<Screen padded={false}>…</Screen>
```

| Prop | Values | Default |
|---|---|---|
| `scroll` | boolean | `false` |
| `padded` | boolean | `true` |
| `safeArea` | boolean | `true` |
| `scrollProps` | `ScrollViewProps` | — |

**States** — none. It sets the theme background, the status-bar glyph colour
(inverted from the theme name), the safe-area inset on iOS and the standard
16px gutter. `scroll` adds a `ScrollView` with bottom padding so the last row
clears a tab bar; leave it off when the screen brings its own `FlatList`.

Two caveats on the platform APIs it uses:

- `safeArea` defaults to `true` but **only does anything on iOS**. React
  Native's `SafeAreaView` is `Platform.select({ ios: native, default: View })`,
  so on Android it pads nothing and content draws under the status bar and the
  navigation bar.
- That import is **deprecated** in `react-native@0.81.4` and logs a warning on
  first mount. Both points are fixed by one swap: when `mobile/` picks up
  `react-native-safe-area-context` (expo-router depends on it), move to
  `useSafeAreaInsets` here — no screen has to change, but it needs a
  `SafeAreaProvider` at the app root and it does change Android's layout.

`StatusBar backgroundColor` is Android-only and, per RN's own types, has no
effect on Android 15+. The glyph style (`barStyle`) is the part that works
everywhere.

## Text

```tsx
<Text variant="title">Highlights</Text>
<Text tone="muted">12 clips</Text>
```

| Prop | Values | Default |
|---|---|---|
| `variant` | `display`, `title`, `heading`, `subheading`, `body`, `label`, `caption`, `micro` | `body` |
| `tone` | `default`, `muted`, `subtle`, `brand`, `onBrand` | `default` |

Plus everything React Native's `Text` takes.

**States** — none. **Use it for every string.** React Native's own `Text`
inherits nothing — no family, no colour, no size — so a bare `<Text>` renders
platform-default black and is invisible in dark mode. That is the whole reason
this primitive exists.

Sizes map to the shared scale: `display` 30, `title` 24, `heading` 18,
`subheading` 16, `body`/`label` 14, `caption` 12, `micro` 10. `micro` also
uppercases (via `textTransform`, so screen readers still get the word) and
widens tracking — it is the badge/section-label style.
