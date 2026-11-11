/**
 * The one upload in flight, shared across pages (CF-70).
 *
 * The transfer itself never depended on the upload page: `uploadFileToR2` is
 * plain XHR with no cancellation, so leaving `/upload` mid-upload always kept
 * the bytes moving. What died with the page was everything *around* it — the
 * progress lived in UploadZone's own state, completion ran `router.push` from
 * wherever the user had got to, and a failure set an error on a component
 * that was no longer mounted. So the attempt lives here instead: a
 * module-level store on the same footing as useMe and gamesCache, read by
 * UploadZone on `/upload` and by UploadStatus everywhere else.
 *
 * Deliberately knows nothing about routing. Navigation on completion is the
 * upload page's decision, taken only while the user is still on it — see
 * UploadZone. This module must never import `next/navigation`.
 *
 * One upload at a time. The api allows more, but a second concurrent upload
 * would need a list here and a strip per entry, for a flow whose common case
 * is one long transfer; `startUpload` refuses while one is in flight.
 */
import { useSyncExternalStore } from "react";

import { completeUpload, createUpload, deleteGame, type UploadTicket } from "./api";
import { addGameToCache } from "./gamesCache";
import { uploadFileToR2 } from "./upload";

export type UploadPhase = "idle" | "uploading" | "finalizing" | "done" | "failed";

export interface UploadSnapshot {
  phase: UploadPhase;
  fileName: string | null;
  sizeBytes: number | null;
  /** The title the game was created with — what the status strip names. */
  title: string | null;
  /** 0–100, or null when no bar should show (idle, failed). */
  progress: number | null;
  statusText: string;
  /** Set on `done`: the game `completeUpload` returned. */
  gameId: string | null;
  /** Set on `failed`. */
  error: string | null;
}

export interface StartUploadOptions {
  title: string;
  condense: boolean;
  /** Browser-probed length, or null when it couldn't be read. */
  duration: number | null;
}

const IDLE: UploadSnapshot = {
  phase: "idle",
  fileName: null,
  sizeBytes: null,
  title: null,
  progress: null,
  statusText: "",
  gameId: null,
  error: null,
};

let _snapshot: UploadSnapshot = IDLE;
const _subscribers = new Set<() => void>();

// Bumped by resetUploadStore(). An attempt started before the bump belongs to
// the previous identity and must go quiet: nothing here can abort the XHR, so
// its continuation would otherwise publish the signed-out user's progress to
// the next user, and — worse — call `completeUpload` under the new session's
// token, claiming the game for whoever is signed in now. Same device as
// useMe's `_generation`.
let _generation = 0;

function _publish(next: UploadSnapshot) {
  _snapshot = next;
  for (const notify of _subscribers) notify();
}

function fmtRemaining(remainingSec: number): string {
  if (remainingSec < 60) return "Uploading — less than a minute left";
  return `Uploading — about ${Math.round(remainingSec / 60)} min left`;
}

/** What the store holds right now, outside React. */
export function getUploadSnapshot(): UploadSnapshot {
  return _snapshot;
}

/** Notified on every publish; returns the unsubscribe. */
export function subscribeUpload(onChange: () => void): () => void {
  _subscribers.add(onChange);
  return () => {
    _subscribers.delete(onChange);
  };
}

function inFlight(phase: UploadPhase): boolean {
  return phase === "uploading" || phase === "finalizing";
}

/**
 * Run the whole upload — ticket, transfer, completion — publishing progress
 * as it goes. Resolves when the attempt has settled either way; the outcome
 * is in the snapshot, not the promise, so a caller that has since unmounted
 * loses nothing by not awaiting it. Rejects only when an upload is already
 * in flight.
 */
export async function startUpload(file: File, opts: StartUploadOptions): Promise<void> {
  if (inFlight(_snapshot.phase)) {
    throw new Error("An upload is already in progress.");
  }

  const generation = _generation;
  const isCurrent = () => generation === _generation;
  // Latched by this attempt's own `done` / `failed`; past that it has nothing
  // left to say. A multipart transfer rejects as soon as one part gives up,
  // but its sibling parts run on — and retry — calling `onProgress` as they
  // go, and nothing here can abort them. Without the latch those stragglers
  // patched their byte ratio and ETA into the failed snapshot, into IDLE once
  // dismissed, and into the retry the user had just started (R14-M1).
  let settled = false;
  // Every write goes through here so a superseded or settled attempt cannot
  // publish.
  const publish = (patch: Partial<UploadSnapshot>) => {
    if (!isCurrent() || settled) return;
    if (patch.phase === "done" || patch.phase === "failed") settled = true;
    _publish({ ..._snapshot, ...patch });
  };

  publish({
    phase: "uploading",
    fileName: file.name,
    sizeBytes: file.size,
    title: opts.title,
    progress: 0, // show the progress bar immediately, not on the first onprogress event
    statusText: "Uploading…",
    gameId: null,
    error: null,
  });

  const start = Date.now();
  let ticket: UploadTicket | null = null;
  // Once completion is in flight the server may have already claimed the row
  // and dispatched the job, so a failure past this point is ambiguous rather
  // than a clean loss — see the catch.
  let completing = false;
  try {
    // 1. Ask the api for a ticket. Type and size are checked here, server
    //    side, before anything is transferred — a rejection costs nothing.
    ticket = await createUpload({
      title: opts.title,
      filename: file.name,
      content_type: file.type,
      size_bytes: file.size,
      condense: opts.condense,
      duration_seconds: opts.duration,
    });

    // 2. Send the video straight to R2. One leg, fully observable: the api
    //    is no longer in the data path, so there is nothing left to guess at
    //    and the countdown is arithmetic on bytes we watched leave.
    const parts = await uploadFileToR2(file, ticket, ({ loaded, total }) => {
      // Held under 100 so a full bar always means "done" — completion below
      // is a real step that can still fail.
      const patch: Partial<UploadSnapshot> = { progress: Math.min(99, (loaded / total) * 100) };
      const elapsed = (Date.now() - start) / 1000;
      if (elapsed > 1 && loaded > 0) {
        patch.statusText = fmtRemaining((total - loaded) / (loaded / elapsed));
      }
      publish(patch);
    });

    // Superseded while the bytes were moving (sign-out, account switch). Stop
    // here rather than complete: `completeUpload` would run under whatever
    // session is live now. The orphaned row is the 24h sweep's to collect.
    if (!isCurrent()) return;

    // 3. Confirm the object landed so the api can queue processing. Nothing
    //    is enqueued before this, so an upload that died never becomes a job.
    publish({ phase: "finalizing", statusText: "Finalizing…" });
    completing = true;
    const game = await completeUpload(ticket.game_id, parts);
    if (!isCurrent()) return;
    // Write the new game straight into the cache so the Library shows it
    // immediately — invalidating alone let a prefetch that started during
    // the (long) upload resolve afterwards and repopulate the cache with a
    // stale list that omitted this game (CF-63).
    addGameToCache(game);
    publish({ phase: "done", progress: 100, statusText: "Uploaded", gameId: game.id });
  } catch (e) {
    if (!isCurrent()) return;
    // Discard the failed attempt server-side. Retrying mints a fresh ticket
    // with a new game id, key and multipart upload, so without this every
    // retry would leave behind a row and — for a large file — its already
    // uploaded parts, billed until the 24h sweep. A dropped 1.5 GB upload on
    // cellular is precisely the case this flow exists for, so retries are
    // expected rather than exceptional.
    //
    // NOT once completion is in flight. `complete_upload` claims the row and
    // dispatches the job before it responds, so a lost response — dropped
    // connection, proxy timeout, backgrounded tab — means the upload may well
    // have succeeded. Deleting there would remove a game the worker is
    // processing right now, turning a recoverable blip into a vanished
    // upload. An ambiguous completion is left alone: the worst case is a row
    // the 24h sweep collects, against silently destroying real work.
    if (ticket && !completing) {
      // Best-effort: the row may already be gone (an oversize upload is
      // deleted server-side), and a cleanup failure must not replace the
      // real error with a confusing one.
      deleteGame(ticket.game_id).catch(() => {});
    }
    publish({
      phase: "failed",
      progress: null,
      statusText: "",
      error: e instanceof Error ? e.message : "Upload failed.",
    });
  }
}

/**
 * Acknowledge a finished or failed upload, clearing it from every surface.
 * A no-op mid-flight: the attempt's own continuation would publish straight
 * over an idle snapshot, so there is nothing coherent to dismiss to.
 */
export function dismissUpload() {
  if (inFlight(_snapshot.phase)) return;
  if (_snapshot.phase !== "idle") _publish(IDLE);
}

/**
 * Drop everything, including an attempt still in flight — call on an identity
 * change so one user's upload never shows to, or completes as, the next.
 */
export function resetUploadStore() {
  _generation++;
  _publish(IDLE);
}

// Idle on the server: the store is per-page module state, so SSR markup can
// never have an upload to show and hydration cannot disagree with it.
const getServerSnapshot = (): UploadSnapshot => IDLE;

/** Subscribe to the active upload. Idle when there is none. */
export function useActiveUpload(): UploadSnapshot {
  return useSyncExternalStore(subscribeUpload, getUploadSnapshot, getServerSnapshot);
}
