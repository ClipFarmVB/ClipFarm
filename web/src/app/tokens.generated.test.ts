import { themeCss } from "@clipfarm/tokens";
import { expect, it } from "vitest";

// tokens.generated.css is not hand-written — it is the rendering of
// packages/tokens, imported by globals.css. Tailwind v4 has no JS config file
// to require the package from, so the wiring is this file snapshot instead:
// edit a token, run `npm run tokens:sync --workspace=web`, commit both.
//
// Without this the stylesheet would be a second copy of the palette, free to
// drift from the one mobile reads. Here a drift fails CI on the next PR.
it("tokens.generated.css matches packages/tokens", async () => {
  await expect(themeCss()).toMatchFileSnapshot("./tokens.generated.css");
});
