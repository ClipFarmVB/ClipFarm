"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, Film, AlertCircle, Loader } from "lucide-react";
import { Button } from "@/components/ui/Button";
import {
  completeUpload,
  createUpload,
  deleteGame,
  getUploadConfig,
  type UploadConfig,
  type UploadTicket,
} from "@/lib/api";
import { uploadFileToR2 } from "@/lib/upload";
import { addGameToCache } from "@/lib/gamesCache";
import { cn } from "@/lib/utils";

// Used only until GET /games/upload-config answers, and as the fallback if it
// never does — the server's values are the real limits. Before CF-163 these
// were the limits, and they disagreed with the server's (15 GB here vs a 2 GB
// cap), so an in-between file was only rejected after the bytes had moved.
const FALLBACK_CONFIG: UploadConfig = {
  max_upload_bytes: 2 * 1024 ** 3,
  allowed_content_types: ["video/mp4", "video/quicktime", "video/x-matroska", "video/webm"],
  single_put_max_bytes: 100 * 1024 ** 2,
  part_size_bytes: 100 * 1024 ** 2,
  url_ttl_seconds: 6 * 3600,
};

function fmtRemaining(remainingSec: number): string {
  if (remainingSec < 60) return "Uploading — less than a minute left";
  return `Uploading — about ${Math.round(remainingSec / 60)} min left`;
}

function fmtLimit(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${Number(gb.toFixed(gb < 10 ? 1 : 0))} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

export function UploadZone() {
  const router = useRouter();
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [condense, setCondense] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [statusText, setStatusText] = useState("Uploading…");
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<UploadConfig>(FALLBACK_CONFIG);

  // Limits come from the api so the advertised cap can't drift from the
  // enforced one. A failure here is not fatal: the fallback keeps the page
  // usable, and the server rejects anything over its real cap at presign time
  // anyway — before a byte moves.
  useEffect(() => {
    let live = true;
    getUploadConfig()
      .then((c) => { if (live) setConfig(c); })
      .catch(() => {});
    return () => { live = false; };
  }, []);

  const validate = (f: File) => {
    if (!config.allowed_content_types.includes(f.type))
      return "Unsupported file type. Upload an MP4, MOV, MKV, or WebM.";
    if (f.size > config.max_upload_bytes)
      return `File too large. Maximum is ${fmtLimit(config.max_upload_bytes)}.`;
    return null;
  };

  const pickFile = useCallback((f: File) => {
    const err = validate(f);
    if (err) { setError(err); return; }
    setError(null);
    setFile(f);
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, ""));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, config]);

  const onDrop = (e: React.DragEvent) => {
    // preventDefault already called by the inline onDrop wrapper below
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) pickFile(f);
  };

  const onFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) pickFile(f);
  };

  const handleUpload = async () => {
    if (!file || uploading) return; // re-entry guard — a second click is a no-op
    setError(null);
    setUploading(true);
    setProgress(0); // show the progress bar immediately, not on the first onprogress event
    setStatusText("Uploading…");

    const start = Date.now();
    let ticket: UploadTicket | null = null;
    try {
      // 1. Ask the api for a ticket. Type and size are checked here, server
      //    side, before anything is transferred — a rejection costs nothing.
      ticket = await createUpload({
        title: title || file.name,
        filename: file.name,
        content_type: file.type,
        size_bytes: file.size,
        condense,
      });

      // 2. Send the video straight to R2. One leg, fully observable: the api
      //    is no longer in the data path, so there is nothing left to guess at
      //    and the countdown is arithmetic on bytes we watched leave.
      const parts = await uploadFileToR2(file, ticket, ({ loaded, total }) => {
        // Held under 100 so a full bar always means "done, redirecting" —
        // completion below is a real step that can still fail.
        setProgress(Math.min(99, (loaded / total) * 100));
        const elapsed = (Date.now() - start) / 1000;
        if (elapsed > 1 && loaded > 0) {
          setStatusText(fmtRemaining((total - loaded) / (loaded / elapsed)));
        }
      });

      // 3. Confirm the object landed so the api can queue processing. Nothing
      //    is enqueued before this, so an upload that died never becomes a job.
      setStatusText("Finalizing…");
      const game = await completeUpload(ticket.game_id, parts);
      setProgress(100);
      // Write the new game straight into the cache so the Library shows it
      // immediately — invalidating alone let a prefetch that started during
      // the (long) upload resolve afterwards and repopulate the cache with a
      // stale list that omitted this game (CF-63).
      addGameToCache(game);
      router.push(`/games/${game.id}`);
    } catch (e) {
      // Discard the failed attempt server-side. Retrying mints a fresh ticket
      // with a new game id, key and multipart upload, so without this every
      // retry would leave behind a row and — for a large file — its already
      // uploaded parts, billed until the 24h sweep. A dropped 1.5 GB upload on
      // cellular is precisely the case this flow exists for, so retries are
      // expected rather than exceptional.
      if (ticket) {
        // Best-effort: the row may already be gone (an oversize upload is
        // deleted server-side), and a cleanup failure must not replace the
        // real error with a confusing one.
        deleteGame(ticket.game_id).catch(() => {});
      }
      setError(e instanceof Error ? e.message : "Upload failed.");
      setProgress(null);
      setUploading(false); // re-enable so the user can retry after a failure
    }
  };

  return (
    <div className="w-full max-w-lg">
      {/* Drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); if (!uploading) setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); if (!uploading) onDrop(e); else setDragging(false); }}
        onClick={() => !file && document.getElementById("file-input")?.click()}
        className={cn(
          "relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-12 text-center transition-all duration-200",
          dragging
            ? "border-brand/50 bg-brand/5 scale-[1.01]"
            : file
            ? "border-border-strong bg-surface cursor-default"
            : "border-border bg-surface hover:border-border-strong hover:bg-surface-high cursor-pointer"
        )}
      >
        <input
          id="file-input"
          type="file"
          accept={config.allowed_content_types.join(",")}
          className="hidden"
          onChange={onFileInput}
          disabled={uploading}
        />

        {file ? (
          <>
            <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-brand/10 border border-brand/20">
              <Film size={18} className="text-brand" />
            </div>
            <p className="text-[14px] font-medium text-foreground">{file.name}</p>
            <p className="mt-1 text-[12px] text-muted">{formatBytes(file.size)}</p>
            {!uploading && (
              <button
                onClick={() => document.getElementById("file-input")?.click()}
                className="mt-3 text-[11px] text-subtle hover:text-muted transition-colors"
              >
                Click to change file
              </button>
            )}
          </>
        ) : (
          <>
            <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-surface-high border border-border">
              <Upload size={16} className="text-muted" />
            </div>
            <p className="text-[14px] font-medium text-foreground">Drop video here</p>
            <p className="mt-1.5 text-[12px] text-muted">
              MP4, MOV, MKV, WebM · up to {fmtLimit(config.max_upload_bytes)}
            </p>
            <p className="mt-3 text-[11px] text-subtle">or click to browse</p>
          </>
        )}
      </div>

      {/* Title input */}
      {file && (
        <div className="mt-4">
          <label className="block text-[12px] font-medium text-muted mb-1.5" htmlFor="game-title">
            Game title
          </label>
          <input
            id="game-title"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={uploading}
            placeholder="e.g. Varsity vs Lincoln — March 25"
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-foreground placeholder:text-subtle focus:border-border-strong focus:outline-none focus:ring-1 focus:ring-border-strong transition-colors disabled:opacity-50"
          />
        </div>
      )}

      {/* Dead-time removal opt-in */}
      {file && (
        <label
          className={cn(
            "mt-3 flex items-start gap-2.5 rounded-md border border-border bg-surface px-3 py-2.5 transition-colors",
            uploading ? "opacity-50" : "cursor-pointer hover:border-border-strong"
          )}
        >
          <input
            type="checkbox"
            checked={condense}
            onChange={(e) => setCondense(e.target.checked)}
            disabled={uploading}
            className="mt-0.5 accent-[var(--brand,#6366f1)]"
          />
          <span>
            <span className="block text-[13px] font-medium text-foreground">Remove dead time</span>
            <span className="mt-0.5 block text-[11px] text-muted">
              Also creates one condensed video with the waiting between rallies cut out. Adds processing time.
            </span>
          </span>
        </label>
      )}

      {/* Error */}
      {error && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2.5 text-[12px] text-red-400">
          <AlertCircle size={13} className="shrink-0 mt-0.5" />
          {error}
        </div>
      )}

      {/* Upload progress */}
      {progress !== null && (
        <div className="mt-4">
          <div className="flex justify-between text-[11px] text-muted mb-1.5">
            <span>{statusText}</span>
            <span className="tabular-nums">{Math.round(progress)}%</span>
          </div>
          <div className="h-0.5 rounded-full bg-surface-high overflow-hidden">
            <div
              className="h-full rounded-full bg-brand transition-all duration-300 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {/* Upload button — stays mounted but disabled while uploading so a
          slow network can't leave a clickable button (double-submit, CF-35) */}
      {file && (
        <Button className="mt-4 w-full" size="lg" onClick={handleUpload} disabled={uploading}>
          {uploading ? <Loader size={16} className="animate-spin" /> : "Upload & process"}
        </Button>
      )}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}
