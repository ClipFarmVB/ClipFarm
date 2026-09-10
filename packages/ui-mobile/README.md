# @clipfarm/ui-mobile

Five React Native primitives — `Button`, `Card`, `Badge`, `Screen`, `Text` —
built on `@clipfarm/tokens`. Every colour, size and distance comes from the
token package, so a screen never writes a hex value or a magic number.

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

Plus everything `Pressable` takes except `style` and `children`.

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
`removed`, `not_an_action`), so a badge matches web exactly. An unrecognised
action degrades to the grey `unknown` chip rather than rendering unstyled.
`not_an_action` displays as "removed", as it does on web.

Unlike the other primitives, badge colours do **not** change with the theme —
the action hues are picked to work on either background, which is how web has
always drawn them.

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
(inverted from the theme name), the safe-area inset and the standard 16px
gutter. `scroll` adds a `ScrollView` with bottom padding so the last row clears
a tab bar; leave it off when the screen brings its own `FlatList`.

Uses React Native's `SafeAreaView`, which is iOS-only. When `mobile/` picks up
`react-native-safe-area-context` (expo-router depends on it), swap it for
`useSafeAreaInsets` here — no screen has to change.

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
