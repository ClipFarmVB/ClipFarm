"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Globe, Lock, Users, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { createPost, type Clip, type Visibility } from "@/lib/api";
import { apiErrorMessage } from "@/lib/apiError";
import { cn } from "@/lib/utils";

const OPTIONS: { value: Visibility; label: string; blurb: string; icon: typeof Lock }[] = [
  {
    value: "private",
    label: "Only me",
    blurb: "Nobody else can see this post.",
    icon: Lock,
  },
  {
    value: "followers",
    label: "Followers",
    blurb: "People who follow you, once following exists.",
    icon: Users,
  },
  {
    value: "public",
    label: "Everyone",
    blurb: "Anyone, including people who aren't signed in.",
    icon: Globe,
  },
];

/**
 * Publish a clip as a post (CF-109).
 *
 * The visibility choice says plainly who will be able to see it rather than
 * naming a tier and leaving the user to guess — this is youth-sports footage,
 * so "Everyone" needs to read as "everyone".
 *
 * Posting never widens the clip itself. If the chosen visibility exceeds what
 * the clip allows the API refuses with 409, and that message is surfaced as-is
 * instead of being silently worked around.
 */
export function PostComposerModal({
  clip,
  onClose,
  onPosted,
}: {
  clip: Clip;
  onClose: () => void;
  onPosted?: () => void;
}) {
  const [caption, setCaption] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("private");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  // The success pause holds a reference to this component for 900ms. Cancelling
  // Post within that window would otherwise fire onClose a second time after
  // unmount — harmless against today's setComposing(false), and a real bug the
  // first time onClose isn't idempotent.
  useEffect(() => {
    return () => { if (closeTimer.current) clearTimeout(closeTimer.current); };
  }, []);

  // Escape closes the composer, not the ClipModal underneath it.
  //
  // What makes that true is ClipModal's `composing` guard, which returns before
  // it reads the key — NOT stopPropagation. Both listeners are on `window`, so
  // neither can stop the other: stopPropagation only halts a bubbling event
  // between DOM nodes, and these are siblings on the same target. The call is
  // gone rather than left in place looking load-bearing.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit() {
    setSaving(true);
    setError(null);
    try {
      await createPost(clip.id, caption, visibility);
      setDone(true);
      onPosted?.();
      closeTimer.current = setTimeout(onClose, 900);
    } catch (e) {
      // Shared helper — it understands the 422 array shape now, so the
      // composer no longer needs a private copy of that logic.
      setError(
        apiErrorMessage(
          (e instanceof Error ? e.message : "").replace(/^API error \d+:\s*/, ""),
          "Could not post",
        ),
      );
    } finally {
      setSaving(false);
    }
  }

  return createPortal(
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4"
      // Click-outside, like every other modal here. Compared against this
      // overlay rather than the parent's, which is why the parent's handler
      // never fired for the composer.
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-background p-5">
        <div className="flex items-start justify-between">
          <h2 className="text-lg font-semibold tracking-tight">Post this clip</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-subtle hover:text-foreground"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        <p className="mt-1 text-xs text-muted">
          Posting doesn&apos;t copy the video — it links to this clip.
        </p>

        <textarea
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          maxLength={500}
          rows={3}
          placeholder="Say something about it…"
          className="mt-4 w-full resize-none rounded-md border border-border bg-surface-high px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-brand"
        />
        <span className="text-[11px] text-subtle">{caption.length}/500</span>

        <div className="mt-4 space-y-1.5">
          {OPTIONS.map(({ value, label, blurb, icon: Icon }) => (
            <button
              key={value}
              type="button"
              onClick={() => setVisibility(value)}
              className={cn(
                "flex w-full items-start gap-2.5 rounded-md border px-3 py-2 text-left transition-colors",
                visibility === value
                  ? "border-brand bg-brand/10"
                  : "border-border hover:border-border-strong",
              )}
            >
              <Icon
                className={cn(
                  "mt-0.5 h-4 w-4 shrink-0",
                  visibility === value ? "text-brand" : "text-subtle",
                )}
              />
              <span className="flex-1">
                <span className="block text-[13px] font-medium">{label}</span>
                <span className="block text-[11px] text-muted">{blurb}</span>
              </span>
            </button>
          ))}
        </div>

        {error && (
          <p className="mt-3 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            {error}
          </p>
        )}

        <div className="mt-5 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={submit} disabled={saving || done}>
            {done ? (
              <>
                <Check className="h-3.5 w-3.5" /> Posted
              </>
            ) : (
              "Post"
            )}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
