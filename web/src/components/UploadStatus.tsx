"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { AlertCircle, Check, Film } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { dismissUpload, useActiveUpload } from "@/lib/uploadStore";

/**
 * The upload in flight, on every page but the upload page (CF-70).
 *
 * Leaving `/upload` mid-upload used to make the transfer invisible: the bytes
 * kept moving, but the only progress bar was on the page just left, and the
 * completion redirected from wherever the user had got to. This strip is the
 * upload's presence elsewhere — progress while it runs, a link to the game
 * when it lands, the error when it fails — and nothing more. It never
 * navigates: that stays with UploadZone, which only does it while the user is
 * still on `/upload`.
 *
 * Inline and non-modal, in the slot ClaimHandleBanner uses, for the same
 * reason OnboardingPanel is: it sits beside whatever the user is doing rather
 * than in front of it.
 */
export function UploadStatus() {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const upload = useActiveUpload();

  // Nothing to say, or the upload page is already saying it in its own layout.
  if (upload.phase === "idle" || !user || loading) return null;
  if (pathname === "/upload" || pathname?.startsWith("/upload/")) return null;

  if (upload.phase === "done" && upload.gameId) {
    return (
      <div
        role="status"
        className="mb-6 flex items-center gap-3 rounded-md bg-brand-dim px-4 py-3"
      >
        <Check className="h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
        {/* The title is the only part that may be clipped: it sits alone in
            the truncating span and the message stays whole from `sm` up. Below
            that the two stack, since the message alone outgrows a phone-width
            column (R5-M1). */}
        <div className="flex min-w-0 flex-1 flex-col gap-x-1 text-sm sm:flex-row">
          <span className="min-w-0 truncate font-medium">&ldquo;{upload.title}&rdquo;</span>
          <span className="sm:shrink-0 sm:whitespace-nowrap">
            <span className="font-medium"> uploaded</span>
            <span className="text-muted"> — processing has started.</span>
          </span>
        </div>
        {/* Opening the game is the acknowledgement; the strip would otherwise
            repeat the news on the page it points at. */}
        <Link
          href={`/games/${upload.gameId}`}
          onClick={dismissUpload}
          className="shrink-0 text-sm font-medium text-brand underline-offset-2 hover:underline"
        >
          View game
        </Link>
        <DismissButton />
      </div>
    );
  }

  if (upload.phase === "failed") {
    return (
      <div
        role="alert"
        className="mb-6 flex items-center gap-3 rounded-md border border-red-500/20 bg-red-500/5 px-4 py-3 text-danger"
      >
        <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1 break-words text-sm">
          <span className="font-medium">&ldquo;{upload.title}&rdquo; didn&rsquo;t upload</span>
          {upload.error && <span> — {upload.error}</span>}
        </div>
        <Link
          href="/upload"
          className="shrink-0 text-sm font-medium underline-offset-2 hover:underline"
        >
          Try again
        </Link>
        <DismissButton />
      </div>
    );
  }

  // Uploading or finalizing. Same bar as UploadZone and the game page, so the
  // upload looks like the same thing wherever it is seen from.
  //
  // Only the phase is announced. The percent and the ETA change every few
  // hundred milliseconds, and a live region around them read progress aloud
  // over whatever page the user had moved on to (R11-N1).
  const progress = upload.progress ?? 0;
  return (
    <div className="mb-6 rounded-md bg-surface px-4 py-3">
      <span className="sr-only" aria-live="polite">
        {upload.phase === "finalizing" ? "Finalizing" : "Uploading"} &ldquo;{upload.title}&rdquo;
      </span>
      <div className="flex items-center gap-3">
        <Film className="h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
        {/* Same shape as the done row: the title truncates, the status and
            its ETA stay whole. The separator only makes sense on one line. */}
        <div className="flex min-w-0 flex-1 flex-col gap-x-1 text-sm sm:flex-row">
          <span className="min-w-0 truncate font-medium">&ldquo;{upload.title}&rdquo;</span>
          <span className="text-muted sm:shrink-0 sm:whitespace-nowrap">
            <span className="hidden sm:inline"> · </span>
            {upload.statusText}
          </span>
        </div>
        <span className="shrink-0 text-xs tabular-nums text-muted">{Math.round(progress)}% sent</span>
      </div>
      <div className="mt-2.5 h-0.5 overflow-hidden rounded-full bg-surface-high">
        <div
          className="h-full rounded-full bg-brand transition-all duration-300 ease-out"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

function DismissButton() {
  return (
    <button
      type="button"
      onClick={dismissUpload}
      className="-my-1 min-h-8 shrink-0 rounded px-2 text-xs text-muted transition-colors hover:bg-surface-high hover:text-foreground focus-visible:outline-2 focus-visible:outline-brand"
    >
      Dismiss
    </button>
  );
}
