"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { Check, Sparkles } from "lucide-react";
import { getUploadConfig, type Game, type UploadConfig } from "@/lib/api";
import { onboardingProgress, useOnboardingRecord, writeOnboarding } from "@/lib/onboarding";
import {
  FALLBACK_UPLOAD_CONFIG,
  formatsLabel,
  fmtLimit,
  fmtMinutes,
} from "@/lib/uploadLimits";
import { cn } from "@/lib/utils";

interface OnboardingPanelProps {
  games: readonly Game[];
  loading: boolean;
  error: string | null;
  userId?: string;
  /**
   * The copied sample game, used as the worked example and excluded from the
   * user's own progress. Null until wired (#220): nothing passes it yet, and
   * the api only sends `is_sample` once #571 lands. A game flagged `is_sample`
   * is excluded from progress either way.
   */
  sampleGameId?: string | null;
}

/**
 * First-upload walkthrough on the Library (CF-221).
 *
 * Inline and non-modal on purpose: it runs alongside the first upload rather
 * than in front of it, so it never traps focus, locks scroll, or gates the
 * "New game" button. Same idiom as ClaimHandleBanner.
 */
export function OnboardingPanel({
  games,
  loading,
  error,
  userId,
  sampleGameId = null,
}: OnboardingPanelProps) {
  const record = useOnboardingRecord(userId);
  const progress = useMemo(() => onboardingProgress(games, sampleGameId), [games, sampleGameId]);

  // Hidden on a fetch error too: a failed list leaves `games` empty, which
  // would otherwise read as a brand-new user.
  const eligible = !loading && !error && Boolean(userId) && record === "none";
  const show = eligible && !progress.ready;

  // The user's own game finished — that is completion. Written to the store,
  // whose notify re-renders this, rather than set as local state.
  useEffect(() => {
    if (eligible && progress.ready && userId) writeOnboarding(userId, "done");
  }, [eligible, progress.ready, userId]);

  // The real limits, so the panel can't advertise a different cap from the
  // one the upload form enforces. A failure keeps the fallback silently.
  const [config, setConfig] = useState<UploadConfig>(FALLBACK_UPLOAD_CONFIG);
  useEffect(() => {
    if (!show) return;
    let live = true;
    getUploadConfig()
      .then((c) => { if (live) setConfig(c); })
      .catch(() => {});
    return () => { live = false; };
  }, [show]);

  if (!show || !userId) return null;

  // The panel unmounts on Skip, so focus falls back to the document body.
  const skip = () => writeOnboarding(userId, "skipped");

  return (
    <section
      aria-labelledby="onboarding-heading"
      className="mb-6 rounded-md border border-brand/30 bg-brand/10 px-4 py-4"
    >
      <div className="flex items-start gap-3">
        <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h2 id="onboarding-heading" className="text-sm font-medium text-foreground">
            Your first game, start to finish
          </h2>
          <p className="mt-0.5 text-[12px] text-muted">
            Three steps from footage to clips.
            {sampleGameId && (
              <>
                {" "}
                <Link
                  href={`/games/${sampleGameId}`}
                  className="font-medium text-brand underline-offset-2 hover:underline"
                >
                  See an example
                </Link>{" "}
                of a finished game first.
              </>
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={skip}
          className="-my-1 min-h-8 shrink-0 rounded px-2 text-[12px] text-muted transition-colors hover:bg-surface-high hover:text-foreground focus-visible:outline-2 focus-visible:outline-brand"
        >
          Skip
        </button>
      </div>

      <ol className="mt-4 space-y-3 sm:pl-7">
        <Step n={1} done={progress.uploaded} title="Upload a full game">
          The whole match recording, not a highlight — clips are cut from it for you.{" "}
          {formatsLabel(config.allowed_content_types)}, up to {fmtLimit(config.max_upload_bytes)} and{" "}
          {fmtMinutes(config.max_duration_seconds / 60)}.{" "}
          {!progress.uploaded && (
            <Link href="/upload" className="font-medium text-brand underline-offset-2 hover:underline">
              Upload a game
            </Link>
          )}
        </Step>
        <Step n={2} done={false} title="Processing can take a while">
          Expect a wait, not seconds. The game&apos;s page shows a
          progress bar with the current stage and an estimate, and you can leave
          the page while it runs. If it fails, the game reads Failed here in the
          Library; upload it again.{" "}
          {progress.inProgressGameId && (
            <Link
              href={`/games/${progress.inProgressGameId}`}
              className="font-medium text-brand underline-offset-2 hover:underline"
            >
              Check on your game
            </Link>
          )}
        </Step>
        <Step n={3} done={false} title="Clips appear on the game's page">
          Once the game reads Ready here in the Library, open it to find your clips.
        </Step>
      </ol>
    </section>
  );
}

function Step({
  n,
  done,
  title,
  children,
}: {
  n: number;
  done: boolean;
  title: string;
  children: ReactNode;
}) {
  return (
    <li className="flex gap-3">
      <span
        className={cn(
          "mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] font-medium tabular-nums",
          done ? "border-brand bg-brand text-[#0c0c0e]" : "border-border-strong bg-surface text-muted",
        )}
      >
        {done ? <Check size={11} strokeWidth={3} aria-hidden="true" /> : n}
        {done && <span className="sr-only">Done:</span>}
      </span>
      <div className="min-w-0">
        <p className={cn("text-[13px] font-medium", done ? "text-muted" : "text-foreground")}>
          {title}
        </p>
        <p className="mt-0.5 text-[12px] leading-relaxed text-muted">{children}</p>
      </div>
    </li>
  );
}
