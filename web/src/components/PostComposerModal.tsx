"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Globe, Lock, Users, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { createPost, type Clip, type Visibility } from "@/lib/api";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { cn } from "@/lib/utils";

const RANK: Record<Visibility, number> = { private: 0, followers: 1, public: 2 };

/**
 * How the ceiling reads in a sentence.
 *
 * The tier names are adjectives in one case and a noun in another, so
 * interpolating them directly produced "this clip is followers". Only the first
 * two can ever render — nothing is blocked when the ceiling is `public` — but
 * the third is here so the map stays total and a new tier is a compile error
 * rather than a sentence that reads wrong in production.
 */
const CEILING_PHRASE: Record<Visibility, string> = {
  private: "this clip is private",
  followers: "this clip is only shared with followers",
  public: "this clip is public",
};

/**
 * Whether `tier` is wider than this clip allows, and so must be offered
 * disabled rather than offered and refused.
 *
 * Exported so it can be asserted: this is the rule that decides whether a user
 * meets a limit they can understand or a 409 they cannot act on, and it mirrors
 * `access.at_most` on the API — the two orderings of the same three values have
 * to agree, and a UI copy that drifts wide is the one that fails on submit.
 *
 * An absent ceiling resolves to `private`, matching `ClipOut`'s own default:
 * a payload that predates the field offers less, never more.
 */
export function tierBlocked(tier: Visibility, ceiling: Visibility | undefined): boolean {
  return RANK[tier] > RANK[ceiling ?? "private"];
}

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
 * Posting never widens the clip itself — and the tiers a clip cannot support
 * are shown disabled, with the reason, rather than offered and then refused.
 *
 * That is the half this was missing. Nothing in the product can raise a clip's
 * or a game's visibility yet (there is no write path for either), and both
 * default to private, so for a real user "Followers" and "Everyone" both ended
 * in a 409 telling them to go do something that does not exist. An unreachable
 * option that explains why is a limit; one that fails on submit is a dead end.
 *
 * `clip.effective_visibility` carries the ceiling the API derives. The 409 is
 * still handled and still surfaced as-is — it stays the backstop for a clip
 * that goes private between the page load and the click, which is exactly the
 * race the server-side check exists for.
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
  // Absent means private — the fail-closed direction, matching the schema's
  // own default. A clip payload that predates this field offers "Only me"
  // rather than offering everything.
  const ceiling: Visibility = clip.effective_visibility ?? "private";

  const [caption, setCaption] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("private");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const captionRef = useRef<HTMLTextAreaElement>(null);

  // A nested overlay has to declare itself one (CF-282), and this did not.
  //
  // ClipModal traps Tab inside its own card, and the trap stack only yields to
  // an inner trap that registers. With none here, ClipModal stayed innermost
  // and kept wrapping Tab through the controls *behind* this dialog: the
  // caption box, the three tiers, Cancel and Post were in nobody's Tab cycle,
  // so a keyboard user could see the composer and reach nothing in it. Focus
  // was never moved in on open or restored on close either.
  //
  // The caption box as initialFocus, not the default first focusable — which
  // here is the X, where the first Space after opening would discard the
  // dialog. ClipModal's comment argues the same point and lands on its card;
  // this one has an obvious safe target, and it is the thing the user opened
  // the composer to type in.
  //
  // onEscape rather than the window listener that used to sit here: it runs on
  // document capture, so no child's stopPropagation can silence it, and it
  // preventDefaults — which the old listener did not, leaving Firefox to
  // revert the caption field on the same keypress that closed the dialog.
  // ClipModal's own `composing` guard still earns its place: it keeps the
  // arrow keys from paging clips underneath while a caption is being typed,
  // and that is not something a focus trap intercepts.
  useFocusTrap(cardRef, true, {
    initialFocus: () => captionRef.current,
    onEscape: onClose,
  });

  // The success pause holds a reference to this component for 900ms. Cancelling
  // Post within that window would otherwise fire onClose a second time after
  // unmount — harmless against today's setComposing(false), and a real bug the
  // first time onClose isn't idempotent.
  useEffect(() => {
    return () => { if (closeTimer.current) clearTimeout(closeTimer.current); };
  }, []);

  async function submit() {
    setSaving(true);
    setError(null);
    try {
      await createPost(clip.id, caption, visibility);
      setDone(true);
      onPosted?.();
      closeTimer.current = setTimeout(onClose, 900);
    } catch (e) {
      // Already decoded. `throwApiError` runs the body through
      // `apiErrorMessage` and throws the result, so `e.message` IS the server's
      // sentence — the 409's "this clip is private, so it can only be posted
      // to private…", or a 422's joined `msg` list.
      //
      // Decoding it a second time here is what this used to do, and it undid
      // the first: `JSON.parse` of a plain sentence throws, so every failure
      // fell back to "Could not post". The 409 is the backstop for a clip that
      // goes private between page load and click — the one case the greyed-out
      // tiers cannot cover — and it was arriving with its reason stripped.
      // Ironically the 422 branch added to `apiErrorMessage` for this composer
      // widened the hole: making the first decode succeed is exactly what makes
      // the second one fail.
      setError(e instanceof Error && e.message ? e.message : "Could not post");
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
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="post-composer-title"
        className="w-full max-w-md rounded-lg border border-border bg-background p-5"
      >
        <div className="flex items-start justify-between">
          <h2 id="post-composer-title" className="text-lg font-semibold tracking-tight">
            Post this clip
          </h2>
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
          ref={captionRef}
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          maxLength={500}
          rows={3}
          placeholder="Say something about it…"
          className="mt-4 w-full resize-none rounded-md border border-border bg-surface-high px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-brand"
        />
        <span className="text-[11px] text-subtle">{caption.length}/500</span>

        <div className="mt-4 space-y-1.5">
          {OPTIONS.map(({ value, label, blurb, icon: Icon }) => {
            const blocked = tierBlocked(value, ceiling);
            return (
              <button
                key={value}
                type="button"
                disabled={blocked}
                aria-describedby={blocked ? `vis-${value}-why` : undefined}
                onClick={() => setVisibility(value)}
                className={cn(
                  "flex w-full items-start gap-2.5 rounded-md border px-3 py-2 text-left transition-colors",
                  blocked
                    ? "cursor-not-allowed border-border/60 opacity-50"
                    : visibility === value
                      ? "border-brand bg-brand/10"
                      : "border-border hover:border-border-strong",
                )}
              >
                <Icon
                  className={cn(
                    "mt-0.5 h-4 w-4 shrink-0",
                    !blocked && visibility === value ? "text-brand" : "text-subtle",
                  )}
                />
                <span className="flex-1">
                  <span className="block text-[13px] font-medium">{label}</span>
                  <span className="block text-[11px] text-muted">
                    {blocked ? (
                      <span id={`vis-${value}-why`}>
                        Not available — {CEILING_PHRASE[ceiling]}, and a post
                        can&apos;t show more of the footage than the clip does.
                      </span>
                    ) : (
                      blurb
                    )}
                  </span>
                </span>
              </button>
            );
          })}
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
