import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
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

// The snapshot above only proves the generated file is right. The @import is
// what makes it reach the browser, and it is now the single thread the whole
// palette hangs from: delete that one line and Tailwind emits no --cf-* custom
// properties and no .bg-brand / .text-foreground / .text-on-brand utilities —
// every screen loses its colours — while lint, typecheck and the rest of this
// suite all still pass. Before this PR the palette lived inside globals.css and
// could not be detached, so the dependency is new and belongs guarded here.
it("globals.css still imports tokens.generated.css", async () => {
  const globals = await readFile(
    fileURLToPath(new URL("./globals.css", import.meta.url)),
    "utf8"
  );
  expect(globals).toContain('@import "./tokens.generated.css";');
});
