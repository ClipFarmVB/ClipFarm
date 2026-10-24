import { describe, expect, it, vi } from "vitest";

import {
  clipDuration,
  createClipFileSource,
  createStaticClipSource,
} from "./clipSource";
import type { ClipSource, PlayableClip } from "./clipSource";

const clip: PlayableClip = {
  id: "clip-1",
  game_id: "game-1",
  clip_url: "https://r2.example/clip-1.mp4?sig=old",
  thumbnail_url: "https://r2.example/clip-1.jpg",
  start_time: 120,
  end_time: 128.5,
};

/**
 * What the player does with a source. Written once, run against both
 * implementations — the actual claim CF-332 makes ("swapping in a second
 * ClipSource requires no player changes") reduced to something a test can fail.
 */
async function play(source: ClipSource, target: PlayableClip) {
  const window = await source.resolve(target);
  return {
    uri: window.uri,
    seekTo: window.startTime,
    playFor: window.endTime - window.startTime,
  };
}

describe("ClipSource", () => {
  it("plays a per-clip file from its own start", async () => {
    expect(await play(createClipFileSource(), clip)).toEqual({
      uri: clip.clip_url,
      seekTo: 0,
      playFor: 8.5,
    });
  });

  it("plays the same clip as a window into one longer file", async () => {
    const source = createStaticClipSource("https://r2.example/game-1-proxy.m3u8");

    expect(await play(source, clip)).toEqual({
      uri: "https://r2.example/game-1-proxy.m3u8",
      seekTo: 120,
      // The same clip, the same duration — only where it lives changed.
      playFor: 8.5,
    });
  });

  it("carries the thumbnail through as a poster", async () => {
    const window = await createClipFileSource().resolve(clip);
    expect(window.posterUri).toBe(clip.thumbnail_url);
  });

  it("refreshes an expired URL through the reloader", async () => {
    const reload = vi.fn(async () => ({
      ...clip,
      clip_url: "https://r2.example/clip-1.mp4?sig=new",
    }));
    const source = createClipFileSource(reload);

    // The app was backgrounded mid-clip and the presigned URL died.
    const window = await source.refresh(clip);

    expect(reload).toHaveBeenCalledWith("clip-1");
    expect(window.uri).toBe("https://r2.example/clip-1.mp4?sig=new");
  });

  it("re-resolves without a reloader rather than failing", async () => {
    // CF-319's refresh endpoint does not exist yet; a player built against this
    // must not crash until it does.
    const window = await createClipFileSource().refresh(clip);
    expect(window.uri).toBe(clip.clip_url);
  });

  it("reports a reversed range as zero rather than negative playback", () => {
    expect(clipDuration({ ...clip, end_time: 100 })).toBe(0);
  });
});
