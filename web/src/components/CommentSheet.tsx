"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import {
  createComment,
  deleteComment,
  getComments,
  type Comment,
  type Post,
} from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { SOCIAL_ENABLED } from "@/lib/features";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { useMe } from "@/lib/useMe";

/**
 * Comments on one post (CF-113): a bottom sheet on a phone, a centred card
 * from `sm` up.
 *
 * Follows `PostComposerModal` — a portal, a `z-[60]` overlay so it sits above
 * the feed's fixed scroller (`z-10`) and the drawer (`z-40`), click-outside on
 * the overlay itself, and a focus trap with the textarea as the initial focus
 * and Escape wired through `onEscape` for the reasons that file's comment
 * gives.
 *
 * Flat comments, newest first, the server's cursor passed back verbatim.
 * Deletion renders only for the comment's author or the post's author — the
 * same rule the API enforces, mirrored here so a user is never offered a
 * control that will 404. `onCountChange` lets the card's rail count follow
 * what happens in here without a refetch.
 */
export function CommentSheet({
  post,
  onClose,
  onCountChange,
}: {
  post: Post;
  onClose: () => void;
  onCountChange?: (delta: number) => void;
}) {
  const { user, loading: authLoading } = useAuth();
  // Gated like every other caller, not `true`. `useMe`'s own docstring says
  // `enabled` is false while signed out precisely to avoid a guaranteed 401 —
  // and `fetchMe` caches only on success, so an unconditional `true` re-fires
  // that 401 on EVERY open of this sheet rather than once. The answer is the
  // same either way (`me === null` shows no delete controls, which is the
  // right non-optimistic reading of both "signed out" and "still loading"),
  // so this is about the requests, not the behaviour.
  const me = useMe(SOCIAL_ENABLED && Boolean(user) && !authLoading);
  const [items, setItems] = useState<Comment[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);
  // Serialises deletes, the way `saving` serialises posting and FeedPost's
  // `likeBusy` serialises the like. Without it a double-tap on a 22px trash
  // icon sends two DELETEs: the second either draws a "not found" banner for a
  // deletion that worked, or — if both clear the pre-check — decrements the
  // card's count twice for one comment.
  const [deleting, setDeleting] = useState<string | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useFocusTrap(cardRef, true, {
    initialFocus: () => textareaRef.current,
    onEscape: onClose,
  });

  async function load(after: string | null) {
    setLoading(true);
    setError(null);
    try {
      const page = await getComments(post.id, after);
      setItems((prev) => {
        const seen = new Set((prev ?? []).map((c) => c.id));
        return [...(prev ?? []), ...page.items.filter((c) => !seen.has(c.id))];
      });
      setCursor(page.next_cursor);
    } catch (e) {
      // Already decoded — `e.message` is the server's sentence (the composer's
      // note on why a second decode undoes the first).
      setError(e instanceof Error && e.message ? e.message : "Could not load comments.");
      setItems((prev) => prev ?? []);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // Reset before loading. `load` APPENDS, and `cursor` is not otherwise
    // cleared, so without this a change of `post.id` on a mounted sheet would
    // staple the new post's comments under the old post's and go on paging with
    // the old post's cursor. Unreachable today — the feed keys cards by id and
    // only appends pages — which is exactly why it is worth writing down rather
    // than relying on.
    setItems(null);
    setCursor(null);
    setError(null);
    void load(null);
    // Only on `post.id`: paging goes through the button below, on the cursor
    // the server handed back.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [post.id]);

  const canSubmit = body.trim().length > 0 && !saving;

  async function submit() {
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      const made = await createComment(post.id, body.trim());
      setItems((prev) => [made, ...(prev ?? [])]);
      setBody("");
      onCountChange?.(+1);
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : "Could not post your comment.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(comment: Comment) {
    if (deleting) return;
    setDeleting(comment.id);
    setError(null);
    try {
      await deleteComment(comment.id);
      setItems((prev) => (prev ?? []).filter((c) => c.id !== comment.id));
      onCountChange?.(-1);
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : "Could not delete that comment.");
    } finally {
      setDeleting(null);
    }
  }

  const canDelete = (comment: Comment) =>
    me !== null && (me.id === comment.author.id || me.id === post.author.id);

  return createPortal(
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/80 sm:items-center sm:p-4"
      onClick={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}
    >
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="comment-sheet-title"
        className="flex max-h-[85vh] w-full max-w-md flex-col rounded-t-lg border border-border bg-background sm:rounded-lg"
      >
        <div className="flex items-start justify-between p-4 pb-2">
          <h2 id="comment-sheet-title" className="text-base font-semibold tracking-tight">
            Comments
          </h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-subtle hover:text-foreground"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        <ul className="flex-1 space-y-3 overflow-y-auto px-4 py-2" aria-busy={loading}>
          {items === null && (
            <li className="py-6 text-center text-[12px] text-muted">Loading…</li>
          )}
          {items !== null && items.length === 0 && !error && (
            <li className="py-6 text-center text-[12px] text-muted">
              No comments yet. Be the first.
            </li>
          )}
          {items?.map((c) => {
            const name = c.author.display_name || (c.author.username ? `@${c.author.username}` : "Someone");
            return (
              <li key={c.id} className="flex items-start gap-2 text-[13px]" data-comment-id={c.id}>
                <div className="min-w-0 flex-1">
                  <span className="font-semibold">{name}</span>{" "}
                  <span className="whitespace-pre-wrap break-words text-foreground/90">{c.body}</span>
                </div>
                {canDelete(c) && (
                  <button
                    type="button"
                    onClick={() => void remove(c)}
                    disabled={deleting !== null}
                    aria-label="Delete comment"
                    className="shrink-0 rounded p-1 text-subtle hover:text-red-400 disabled:opacity-40"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </li>
            );
          })}
          {cursor && (
            <li className="pt-1 text-center">
              <Button variant="ghost" size="sm" onClick={() => void load(cursor)} disabled={loading}>
                {loading ? "Loading…" : "Load more"}
              </Button>
            </li>
          )}
        </ul>

        {error && (
          // Announced: in a modal this banner is the ONLY feedback for a failed
          // post or delete, and focus stays in the textarea, so a screen-reader
          // user would otherwise get silence where a sighted one gets a reason.
          <p
            role="alert"
            className="mx-4 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
          >
            {error}
          </p>
        )}

        <div className="border-t border-border p-3">
          <textarea
            ref={textareaRef}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter keeps a newline, since a comment is
              // usually one line and a phone keyboard's return key is the
              // send button people reach for.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            maxLength={500}
            rows={2}
            placeholder="Add a comment…"
            aria-label="Add a comment"
            className="w-full resize-none rounded-md border border-border bg-surface-high px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-brand"
          />
          <div className="mt-2 flex items-center justify-between">
            <span className="text-[11px] text-subtle">{body.length}/500</span>
            <Button size="sm" onClick={() => void submit()} disabled={!canSubmit}>
              {saving ? "Posting…" : "Post"}
            </Button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
