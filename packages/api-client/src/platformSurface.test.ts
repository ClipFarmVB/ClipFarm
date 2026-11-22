/**
 * CF-314: the package's platform contract, enforced rather than described.
 *
 * The package compiles against the `DOM` lib because that is where TypeScript
 * keeps the declarations of `fetch`, `URLSearchParams` and `FormData` — but a
 * React Native consumer has only the parts of that lib RN implements, and RN's
 * bundled `URLSearchParams`, for one, has `append` and `toString` and throws on
 * `set`. So "uses the DOM lib" is not a contract a consumer can act on, and
 * "uses no DOM API" is not true. The contract is the exact list below.
 *
 * This suite asks the type checker which `lib.dom` declarations the package's
 * non-test source resolves to — globals, and the members read off them — and
 * requires that set to equal the list. A new DOM global, or a new method on an
 * allowed one, fails here until someone adds it to the list on purpose, having
 * checked that React Native has it; a name that stops being used fails too, so
 * the list never claims more than the code needs. `README.md`'s "What it needs
 * from the host" section is the human-readable copy of this list.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

/**
 * Every `lib.dom` name the package may touch. `(type)` marks a name used only
 * in a type position, which costs a consumer nothing at runtime.
 */
const ALLOWED_DOM_SURFACE = [
  "Body.json",
  "Body.text",
  "File (type)",
  "FormData",
  "FormData.append",
  "Headers.get",
  "RequestInit (type)",
  "RequestInit.headers",
  "Response (type)",
  "Response.headers",
  "Response.ok",
  "Response.status",
  "URLSearchParams",
  "URLSearchParams.append",
  "URLSearchParams.toString",
  "fetch",
];

const LIB_DOM = /[\\/]lib\.dom(\.iterable)?\.d\.ts$/;

function srcDir(): string {
  // `import.meta.url` rather than `node:path`/`node:url`: the package's
  // tsconfig has `"types": []`, so Node's module typings are not available to
  // a file it typechecks. Strip the leading slash a Windows drive path gets.
  return decodeURIComponent(new URL(".", import.meta.url).pathname).replace(
    /^\/(?=[A-Za-z]:)/,
    "",
  );
}

function isInTypePosition(node: ts.Node): boolean {
  for (let p: ts.Node | undefined = node.parent; p; p = p.parent) {
    if (ts.isTypeNode(p)) return true;
  }
  return false;
}

/** The `lib.dom` declarations the package's source resolves to, by name. */
function domSurface(): Map<string, string[]> {
  const dir = srcDir();
  const configPath = ts.findConfigFile(dir, ts.sys.fileExists);
  if (!configPath) throw new Error(`no tsconfig.json above ${dir}`);
  const config = ts.readConfigFile(configPath, ts.sys.readFile).config;
  const parsed = ts.parseJsonConfigFileContent(
    config,
    ts.sys,
    configPath.replace(/[\\/][^\\/]+$/, ""),
  );
  const sources = parsed.fileNames.filter((f) => !f.endsWith(".test.ts"));
  // Guards the guard: if the file list came back empty the suite would pass on
  // nothing, so require the module that does the requests to be in it.
  if (!sources.some((f) => /[\\/]api\.ts$/.test(f))) {
    throw new Error(`api.ts not among the scanned sources: ${sources.join(", ")}`);
  }

  const program = ts.createProgram(sources, parsed.options);
  const checker = program.getTypeChecker();
  const found = new Map<string, string[]>();

  for (const file of sources) {
    const sf = program.getSourceFile(file);
    if (!sf) throw new Error(`not in the program: ${file}`);
    const visit = (node: ts.Node): void => {
      // A member reached as `params["set"](...)` is a string literal, not an
      // identifier, and is the same call at runtime — so resolve that too.
      const isElementKey =
        ts.isStringLiteralLike(node) &&
        ts.isElementAccessExpression(node.parent) &&
        node.parent.argumentExpression === node;
      if (ts.isIdentifier(node) || isElementKey) {
        let symbol = checker.getSymbolAtLocation(node);
        if (symbol && symbol.flags & ts.SymbolFlags.Alias) {
          symbol = checker.getAliasedSymbol(symbol);
        }
        const fromDom = symbol?.declarations?.some((d) =>
          LIB_DOM.test(d.getSourceFile().fileName),
        );
        if (symbol && fromDom) {
          const name =
            checker.getFullyQualifiedName(symbol) +
            (isInTypePosition(node) ? " (type)" : "");
          const line = sf.getLineAndCharacterOfPosition(node.getStart()).line + 1;
          const where = `${sf.fileName.replace(/^.*[\\/]/, "")}:${line}`;
          found.set(name, [...(found.get(name) ?? []), where]);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }
  return found;
}

describe("the package's DOM surface", () => {
  // One program over the package is a couple of seconds of tsc work.
  const found = domSurface();

  it("uses no lib.dom name outside the allowed list", () => {
    const extra = [...found]
      .filter(([name]) => !ALLOWED_DOM_SURFACE.includes(name))
      .map(([name, where]) => `${name} at ${where.join(", ")}`);
    expect(extra).toEqual([]);
  });

  it("lists nothing the package no longer uses", () => {
    const unused = ALLOWED_DOM_SURFACE.filter((name) => !found.has(name));
    expect(unused).toEqual([]);
  });
});
