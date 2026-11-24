/**
 * The landing page's demo video (CF-219, absorbing CF-66).
 *
 * Two optional env vars, both public URLs, normally on the R2 media host:
 *
 *   NEXT_PUBLIC_DEMO_VIDEO_URL   the video file. Encode it faststart (moov atom
 *                                first) and serve it with HTTP range support,
 *                                or the player cannot start before the whole
 *                                file has downloaded.
 *   NEXT_PUBLIC_DEMO_POSTER_URL  the poster frame shown before play.
 *
 * Unset or blank, the page renders a placeholder panel instead of a player.
 *
 * `NEXT_PUBLIC_*` values are inlined at build time, so setting or changing
 * either one needs a rebuild and redeploy of the web service — a restart
 * alone keeps whatever the last build baked in.
 *
 * Read at call time rather than at module load so tests can stub the env.
 */
export interface DemoMedia {
  videoUrl: string;
  posterUrl: string | null;
}

function nonBlank(value: string | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

export function demoMedia(): DemoMedia | null {
  const videoUrl = nonBlank(process.env.NEXT_PUBLIC_DEMO_VIDEO_URL);
  if (!videoUrl) return null;
  return { videoUrl, posterUrl: nonBlank(process.env.NEXT_PUBLIC_DEMO_POSTER_URL) };
}
