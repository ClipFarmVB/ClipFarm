/**
 * The duration of the video at `url`, read from its metadata by an off-screen
 * <video> — the clip modal's only way to tell that a trim's background re-cut
 * has landed (CF-272). The api answers a trim before the re-cut runs and gives
 * no signal when it finishes, and the re-cut overwrites the same storage key,
 * so a URL for the clip serves the old cut until then and the new one after:
 * the length is what changes.
 *
 * `preload="metadata"` with `+faststart` (ml/pipeline/clip.py) fetches the moov
 * box, not the clip. Rejects on a load error or after `timeoutMs`; the element
 * is emptied either way so it holds no connection open.
 */
export function probeDuration(url: string, timeoutMs = 15_000): Promise<number> {
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      video.removeAttribute("src");
      try {
        video.load();
      } catch {
        // Emptying the element is tidy-up; a failure here changes nothing.
      }
      fn();
    };
    const timer = setTimeout(() => finish(() => reject(new Error("timed out"))), timeoutMs);
    video.addEventListener("loadedmetadata", () => {
      const d = video.duration;
      finish(() => (Number.isFinite(d) ? resolve(d) : reject(new Error("no duration"))));
    });
    video.addEventListener("error", () => finish(() => reject(new Error("load failed"))));
    video.src = url;
  });
}
