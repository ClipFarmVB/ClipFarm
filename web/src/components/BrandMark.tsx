import { Clapperboard } from "lucide-react";

/**
 * The ClipFarm mark and wordmark, as they appear in the sidebar's two
 * placements — the mobile top bar and the desktop rail (CF-260).
 *
 * Extracted so that swapping the placeholder `Clapperboard` for the real logo
 * is one edit rather than two identical ones fifty lines apart (CF-248).
 *
 * Only the mark and wordmark live here. The wrapping <Link> stays at each call
 * site because the two differ for real reasons: the rail's version closes the
 * drawer on navigate and shares a flex row with a close button.
 *
 * The hover state is `group-hover:`, so it relies on that wrapping <Link>
 * carrying `group`. Both call sites do — noted here rather than left to be
 * rediscovered by breaking it.
 */
export function BrandMark() {
  return (
    <>
      <div className="flex h-[26px] w-[26px] items-center justify-center rounded-md bg-brand/10 transition-colors group-hover:bg-brand/20">
        <Clapperboard size={13} className="text-brand" strokeWidth={2.5} />
      </div>
      <span className="text-[14px] font-semibold tracking-tight text-foreground">
        ClipFarm
      </span>
    </>
  );
}
