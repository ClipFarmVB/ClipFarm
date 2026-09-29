/**
 * Split a demo footage credit into text and link parts (CF-565).
 *
 * The credit is operator-set text from `SAMPLE_GAME_CREDIT`, e.g.
 * "Footage: Riverside Hawks — https://www.youtube.com/watch?v=…". It is never
 * rendered as HTML: the caller renders `text` parts as text and `link` parts
 * as an <a>. Only an absolute `http:` or `https:` URL becomes a link, so a
 * `javascript:` or `data:` value can never reach an href.
 */
export type CreditPart = { kind: "text"; text: string } | { kind: "link"; text: string; href: string };

// A candidate is any run of non-space characters that starts like a URL; the
// scheme check below is what decides. Trailing sentence punctuation (ASCII,
// curly quotes and the ellipsis) is not part of the link.
const CANDIDATE = /\b[a-z][a-z0-9+.-]*:\/\/[^\s<>"]+/gi;
const TRAILING = /[.,;:!?'"‘’“”…\]]+$/;

/** Trim trailing punctuation, and a `)` only when the URL has no `(` for it. */
function trimTrailing(raw: string): string {
  let url = raw.replace(TRAILING, "");
  while (url.endsWith(")")) {
    const opens = (url.match(/\(/g) ?? []).length;
    const closes = (url.match(/\)/g) ?? []).length;
    if (closes <= opens) break;
    url = url.slice(0, -1).replace(TRAILING, "");
  }
  return url;
}

function safeHref(raw: string): string | null {
  try {
    const url = new URL(raw);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export function splitCredit(credit: string | null | undefined): CreditPart[] {
  const text = (credit ?? "").trim();
  if (!text) return [];

  const parts: CreditPart[] = [];
  let last = 0;
  for (const match of text.matchAll(CANDIDATE)) {
    const start = match.index ?? 0;
    const raw = trimTrailing(match[0]);
    const href = safeHref(raw);
    if (!href) continue;
    if (start > last) parts.push({ kind: "text", text: text.slice(last, start) });
    parts.push({ kind: "link", text: raw, href });
    last = start + raw.length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}
