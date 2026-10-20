import { demoMedia } from "@/lib/demoMedia";

/**
 * The landing page's demo video slot (CF-66 folded into CF-219).
 *
 * A native <video>, not the app's video.js player: this is one file, and the
 * landing page should not pull in a player bundle for it. `preload="none"`
 * keeps the visit from downloading anything until the visitor presses play;
 * there is deliberately no autoplay, muted or otherwise.
 *
 * With no URL configured it renders a placeholder panel of the same shape, so
 * the page layout does not change the day the video arrives.
 */
export function DemoVideo() {
  const media = demoMedia();

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      {media ? (
        <video
          className="block aspect-video w-full bg-black"
          src={media.videoUrl}
          poster={media.posterUrl ?? undefined}
          controls
          preload="none"
          playsInline
        >
          Your browser cannot play this video.
        </video>
      ) : (
        <div
          role="img"
          aria-label="Demo video"
          data-testid="demo-video-placeholder"
          className="relative flex aspect-video w-full items-center justify-center"
        >
          <div className="dot-grid absolute inset-0 pointer-events-none" />
          <div className="relative flex flex-col items-center gap-3">
            <div className="flex h-14 w-14 items-center justify-center rounded-full border border-brand/30 bg-brand-dim">
              <svg className="ml-1 h-5 w-5 fill-brand" viewBox="0 0 24 24" aria-hidden>
                <path d="M8 5v14l11-7z" />
              </svg>
            </div>
            <p className="text-[12px] font-medium text-muted">Demo video</p>
          </div>
        </div>
      )}
    </div>
  );
}
