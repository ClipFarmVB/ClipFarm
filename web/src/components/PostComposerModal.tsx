"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Globe, Lock, Users, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { createPost, type Clip, type Visibility } from "@/lib/api";
import { PUBLIC_POSTING_ENABLED } from "@/lib/features";
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
 * Whether posting at `tier` would also have to widen the clip.
 *
 * Was `tierBlocked`, and the rename is the change CF-109b makes: this used to
 * decide what to grey out, because nothing could raise a clip's visibility and
 * the wider tiers were simply unreachable. Now they are reachable, so the same
 * arithmetic decides what needs the user's consent instead. Keeping the old
 * name would have left every reader thinking these tiers are still refused.
 *
 * Exported so it can be asserted: it mirrors `access.at_most` on the API, the
 * two orderings of the same three values have to agree, and a UI copy that
 * drifts wide is the one that fails on submit.
 *
 * An absent ceiling resolves to `private`, matching `ClipOut`'s own default: a
 * payload that predates the field asks for consent it may not need, rather than
 * silently widening footage it could not read the tier of.
 */
export function tierNeedsRaise(
  tier: Visibility,
  ceiling: Visibility | undefined,
): boolean {
  return RANK[tier] > RANK[ceiling ?? "private"];
}

/**
 * Whether this deployment offers `tier` at all.
 *
 * Only `public`, and only while `PUBLIC_POSTING_ENABLED` is off. Unlike the
 * ceiling this is not something the user can act on from here — no consent
 * makes it available — so it stays a disabled option with a reason, which is
 * what the ceiling used to be.
 */
export function tierUnavailable(tier: Visibility): boolean {
  return tier === "public" && !PUBLIC_POSTING_ENABLED;
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
 * For two releases nothing in the product could raise a clip's visibility at
 * all, and both a clip and its game default to private, so for a real user
 * "Followers" and "Everyone" both ended in a 409 telling them to go do
 * something that does not exist. CF-109 greyed those tiers out instead, on the
 * argument that an unreachable option explaining why is a limit while one that
 * fails on submit is a dead end.
 *
 * CF-109b (#398) built the write path, so they are reachable now and this
 * offers them. Two rules, deliberately kept apart:
 *
 * - **Wider than the clip** is no longer a refusal, it is a request for
 *   consent. Picking the tier is not enough: widening the clip changes what
 *   people can see of the FOOTAGE, which outlives the post and is not undone by
 *   deleting it, so the checkbox says exactly that and Post stays disabled
 *   until it is ticked. `create_post` takes the raise as a flag on the same
 *   request, so a post that fails to insert cannot leave the clip widened.
 * - **`public` while the deployment has it off** is still a refusal, because no
 *   consent from here makes it available. It stays a disabled option with a
 *   reason — what the ceiling used to be.
 *
 * The 409 is still handled and still surfaced as-is. It remains the backstop
 * for a clip narrowed between the page load and the click, which is exactly the
 * race the server-side check exists for, and the 422 is the backstop for the
 * two `PUBLIC_POSTING_ENABLED` flags disagreeing.
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
  // Consent to widen the clip along with the post (CF-109b). Reset by
  // `choose` on every tier change, deliberately: a tick that survived a change
  // of mind would be consent to something the user was no longer looking at.
  const [raiseConsent, setRaiseConsent] = useState(false);

  const needsRaise = tierNeedsRaise(visibility, ceiling);
  const selectedLabel =
    OPTIONS.find((o) => o.value === visibility)?.label ?? visibility;

  function choose(tier: Visibility) {
    setVisibility(tier);
    // Consent belongs to the tier it was given for. Carrying a tick from
    // "Followers" over to "Everyone" would widen the clip further than the
    // user agreed to, without asking again.
    setRaiseConsent(false);
  }
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
      await createPost(clip.id, caption, visibility, needsRaise);
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
            // Only the deployment flag disables an option now. A tier above
            // the clip's ceiling is offered and asks for consent below, which
            // is the whole of CF-109b item 1 on this side.
            const blocked = tierUnavailable(value);
            return (
              <button
                key={value}
                type="button"
                disabled={blocked}
                aria-describedby={blocked ? `vis-${value}-why` : undefined}
                onClick={() => choose(value)}
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
                        Not available on this app yet. You can share with your
                        followers instead.
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

        {needsRaise && (
          // The explicit confirmation CF-109 named as the alternative it was
          // not taking, and CF-109b built. Selecting the tier is not on its own
          // consent to widen the footage behind it: this changes what people
          // can see of the CLIP, which outlives the post and is not undone by
          // deleting it. So it says exactly what changes, and the button stays
          // disabled until it is ticked.
          <label
            id="raise-consent"
            className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200/90"
          >
            <input
              type="checkbox"
              checked={raiseConsent}
              onChange={(e) => setRaiseConsent(e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-amber-400"
            />
            <span>
              {CEILING_PHRASE[ceiling]}. Posting to{" "}
              <strong className="font-semibold">{selectedLabel}</strong> will
              also change the clip itself, so it stays visible to them after
              this post is deleted. You can make it private again from the clip.
            </span>
          </label>
        )}

        {error && (
          // Announced. The 409 and the 422 are this component's whole backstop
          // — a clip narrowed between page load and click, and the two
          // PUBLIC_POSTING_ENABLED flags disagreeing — and focus stays on the
          // Post button, so without a live region they are invisible to a
          // screen-reader user.
          <p
            role="alert"
            className="mt-3 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
          >
            {error}
          </p>
        )}

        <div className="mt-5 flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={submit}
            disabled={saving || done || (needsRaise && !raiseConsent)}
            // Points at the consent block when that is what is holding it,
            // the same way a blocked tier points at its own reason. A disabled
            // button is out of the tab order and explains nothing on its own,
            // so without this a screen-reader user meets a Post they cannot
            // reach and no statement of why.
            aria-describedby={needsRaise && !raiseConsent ? "raise-consent" : undefined}
          >
            {done ? (
              <>
                <Check className="h-3.5 w-3.5" />{" "}
                <span role="status">Posted</span>
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
