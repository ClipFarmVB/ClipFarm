import type { UploadConfig } from "@/lib/api";

// Shared by the upload page and the first-upload onboarding panel (CF-221), so
// the limits a new user is told about are the ones the upload form enforces.

// Used only until GET /games/upload-config answers, and as the fallback if it
// never does — the server's values are the real limits. Before CF-163 these
// were the limits, and they disagreed with the server's (15 GB here vs a 2 GB
// cap), so an in-between file was only rejected after the bytes had moved.
// Keep these in step with api/app/config.py: a fallback above the real cap
// would reintroduce exactly that failure whenever the config request fails.
export const FALLBACK_UPLOAD_CONFIG: UploadConfig = {
  max_upload_bytes: 8 * 1024 ** 3,
  allowed_content_types: ["video/mp4", "video/quicktime", "video/x-matroska", "video/webm"],
  single_put_max_bytes: 100 * 1024 ** 2,
  part_size_bytes: 100 * 1024 ** 2,
  url_ttl_seconds: 12 * 3600,
  // Quota fallbacks are deliberately "nothing used yet": if the config never
  // arrives we must not invent a limit and block a legitimate upload. The
  // readout is hidden in that case (see `quotaKnown` in UploadZone.tsx), and the server enforces
  // the real numbers at presign regardless.
  max_duration_seconds: 4 * 3600,
  window_hours: 24,
  max_games_per_window: 5,
  games_used: 0,
  games_remaining: 5,
  max_minutes_per_window: 360,
  minutes_used: 0,
  minutes_remaining: 360,
};

/**
 * The fallback's formats as a user reads them. Used only when `formatsLabel`
 * gets an empty list.
 */
export const SUPPORTED_FORMATS_LABEL = "MP4, MOV, MKV, WebM";

// Key order is display order, and matches SUPPORTED_FORMATS_LABEL.
const FORMAT_NAMES: Record<string, string> = {
  "video/mp4": "MP4",
  "video/quicktime": "MOV",
  "video/x-matroska": "MKV",
  "video/webm": "WebM",
};

/**
 * The formats `GET /games/upload-config` admits (`allowed_content_types`), as
 * a user reads them. The order is fixed, not the server's: the api sorts the
 * types (`api/app/services/quota.py`), which would read "MP4, MOV, WebM, MKV"
 * and reorder the label on screen when the response replaces the fallback. So
 * the named formats come first in `FORMAT_NAMES` order (MP4, MOV, MKV, WebM),
 * keeping only those the server allows, then any other types in the server's
 * order. A type without a friendly name shows as its MIME subtype with any
 * `x-` prefix stripped, uppercased (`video/x-msvideo` reads `MSVIDEO`), rather
 * than being dropped, so a non-empty list is never under-reported. An empty
 * list is the exception: it returns `SUPPORTED_FORMATS_LABEL`, which names
 * four formats although the server then accepts none.
 */
export function formatsLabel(contentTypes: readonly string[]): string {
  const allowed = new Set(contentTypes);
  const named = Object.keys(FORMAT_NAMES);
  const known = named.filter((t) => allowed.has(t)).map((t) => FORMAT_NAMES[t]);
  const unknown = contentTypes
    .filter((t) => !named.includes(t))
    .map((t) => (t.split("/").pop() ?? t).replace(/^x-/, "").toUpperCase());
  const unique = [...new Set([...known, ...unknown].filter(Boolean))];
  return unique.length > 0 ? unique.join(", ") : SUPPORTED_FORMATS_LABEL;
}

export function fmtMinutes(minutes: number): string {
  const m = Math.max(0, Math.round(minutes));
  if (m < 60) return `${m} min`;
  return m % 60 === 0 ? `${m / 60} h` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

/** Mirrors quota.fmt_size in the api — change the two together. */
export function fmtLimit(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${Number(gb.toFixed(gb < 10 ? 1 : 0))} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}
