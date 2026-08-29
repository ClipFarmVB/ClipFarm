/**
 * The `ClipSource` contract (CF-315), for the player in CF-332.
 *
 * Today the backend cuts one video file per clip, and playback is "fetch that
 * URL and play it end to end". The mezzanine-proxy epic (CF-51) replaces that
 * with a `(proxy_url, start, end)` window over a single transcode of the whole
 * game — one file, many clips, played by seeking.
 *
 * Both are the same shape if the player never assumes a clip owns its file:
 * every source resolves a clip to a **playback window** — a URI plus the offsets
 * within it — and the player seeks to `startTime` and stops at `endTime`. With
 * a file per clip those offsets are 0 and the clip's duration, and seeking to 0
 * costs nothing. So the player is written once and the proxy slots in behind
 * it, which is also why CF-332 does not wait on CF-51.
 */

/** The fields of a clip that playback needs. A subset of the api's `Clip`. */
export interface PlayableClip {
  id: string;
  game_id: string;
  /** The clip's own file, as the api serves it today. */
  clip_url: string;
  thumbnail_url?: string;
  /**
   * Offsets **within the game**, which is how the api reports them. The file
   * source discards them; a proxy source is built on them.
   */
  start_time: number;
  end_time: number;
}

export interface PlaybackWindow {
  /** What the video element loads. */
  uri: string;
  /** Seconds into `uri` at which the clip starts. */
  startTime: number;
  /** Seconds into `uri` at which it ends. */
  endTime: number;
  posterUri?: string;
  /**
   * Epoch milliseconds after which `uri` stops working, when the source knows.
   * Undefined means unknown — not "never expires". Playback URLs are presigned
   * and short-lived, so a player that only ever checks this will still meet a
   * dead URL; `refresh` is the recovery either way (CF-319).
   */
  expiresAt?: number;
}

export interface ClipSource {
  /** The window to play. May be served from cache. */
  resolve(clip: PlayableClip): Promise<PlaybackWindow>;
  /**
   * A fresh window for a clip whose URI has expired — the case where the app
   * was backgrounded mid-clip and the presigned URL died while it was away.
   * Never cached.
   */
  refresh(clip: PlayableClip): Promise<PlaybackWindow>;
}

/** Seconds. Zero-length or reversed ranges are a server bug, not a UI state. */
export function clipDuration(clip: PlayableClip): number {
  return Math.max(0, clip.end_time - clip.start_time);
}

/**
 * Today's backend: one file per clip, played from its own start.
 *
 * `refresh` re-asks for the clip through `reload`, which CF-319 will point at
 * its refresh endpoint. Until that exists the caller can leave it out, and a
 * refresh simply re-resolves what it already has — no worse than today, and no
 * player change when the endpoint arrives.
 */
export function createClipFileSource(
  reload?: (clipId: string) => Promise<PlayableClip>
): ClipSource {
  const windowFor = (clip: PlayableClip): PlaybackWindow => ({
    uri: clip.clip_url,
    startTime: 0,
    endTime: clipDuration(clip),
    ...(clip.thumbnail_url ? { posterUri: clip.thumbnail_url } : {}),
  });

  return {
    async resolve(clip) {
      return windowFor(clip);
    },
    async refresh(clip) {
      return windowFor(reload ? await reload(clip.id) : clip);
    },
  };
}

/**
 * A source over a fixed URI — the mock.
 *
 * It exists to prove the interface holds up under a second implementation
 * before the proxy is the one proving it, and it is what a player test or a
 * screen demo plays: no network, no presigned URL to expire, and windows that
 * are genuinely offsets into a longer file, the way the proxy's will be.
 */
export function createStaticClipSource(uri: string, posterUri?: string): ClipSource {
  const windowFor = (clip: PlayableClip): PlaybackWindow => ({
    uri,
    startTime: clip.start_time,
    endTime: clip.end_time,
    ...(posterUri ? { posterUri } : {}),
  });

  return {
    async resolve(clip) {
      return windowFor(clip);
    },
    async refresh(clip) {
      return windowFor(clip);
    },
  };
}
