"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, Film, AlertCircle, Loader } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { uploadGame } from "@/lib/api";
import { addGameToCache } from "@/lib/gamesCache";
import { cn } from "@/lib/utils";

const ACCEPTED = ["video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"];
const MAX_SIZE_GB = 15;

// Assumed throughput of the server's upload to R2 — the "finalizing" leg the
// browser can't observe. Used only to *estimate* that leg's duration so the
// bar keeps moving instead of freezing. The real rate depends on the server's
// uplink, so it's configurable via NEXT_PUBLIC_FINALIZE_MBPS (defaulting to
// the ~3.4 MB/s measured in dev). A wrong value only affects pacing, never
// correctness — the bar always snaps to 100% when the server actually responds.
const _finalizeMbps = Number(process.env.NEXT_PUBLIC_FINALIZE_MBPS);
const FINALIZE_BYTES_PER_SEC =
  (Number.isFinite(_finalizeMbps) && _finalizeMbps > 0 ? _finalizeMbps : 3.4) * 1024 * 1024;

// Countdown shown while the server is still uploading to R2 (the estimated
// leg). "Finalizing…" is deliberately NOT used here — it's reserved for when
// the estimate elapses and we're only waiting on the server's confirmation.
function fmtRemaining(remainingSec: number): string {
  if (remainingSec < 60) return "Uploading — less than a minute left";
  return `Uploading — about ${Math.round(remainingSec / 60)} min left`;
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
  // Bar spans both upload legs; refs survive re-renders during a single upload.
  const pctRef = useRef(0);                                    // monotonic — never rewind
  const finalizeTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => {  // stop the finalize animation if we unmount mid-upload
    if (finalizeTimer.current) clearInterval(finalizeTimer.current);
  }, []);

  const validate = (f: File) => {
    if (!ACCEPTED.includes(f.type)) return "Unsupported file type. Upload an MP4, MOV, AVI, or WebM.";
    if (f.size > MAX_SIZE_GB * 1024 ** 3) return `File too large. Maximum is ${MAX_SIZE_GB} GB.`;
    return null;
  };

  const pickFile = useCallback((f: File) => {
    const err = validate(f);
    if (err) { setError(err); return; }
    setError(null);
    setFile(f);
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, ""));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title]);

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
    pctRef.current = 0;
    setProgress(0); // show the progress bar immediately, not on the first onprogress event
    setStatusText("Uploading…");

    const sendStart = Date.now();
    // Estimated duration of the invisible server→R2 leg (see constant above).
    const estFinalizeSec = file.size / FINALIZE_BYTES_PER_SEC;
    // Advance the bar but never let it rewind or hit 100% before the server
    // actually confirms — the estimate is a guide, the response is the truth.
    const advance = (pct: number) => {
      pctRef.current = Math.max(pctRef.current, Math.min(pct, 99));
      setProgress(pctRef.current);
    };

    try {
      const game = await uploadGame(file, title || file.name, condense, (p) => {
        if (p.phase === "sending") {
          // Weight the two legs by their estimated time: leg 1 (send) is
          // measured live from throughput, leg 2 (finalize) from the estimate.
          const elapsed = (Date.now() - sendStart) / 1000;
          // Guard loaded>0: a 0-byte progress event would make this Infinity →
          // NaN, and NaN sticks through Math.max, freezing the bar permanently.
          const estSendSec = elapsed > 0.2 && p.loaded > 0 ? p.total / (p.loaded / elapsed) : 0;
          const sendShare = estSendSec / (estSendSec + estFinalizeSec || 1);
          advance((p.loaded / p.total) * sendShare * 100);
        } else {
          // Finalizing: animate across the estimate with a live countdown.
          const actualSendSec = Math.max((Date.now() - sendStart) / 1000, 0.001);
          const finalizeStart = Date.now();
          const total = actualSendSec + estFinalizeSec;
          if (finalizeTimer.current) clearInterval(finalizeTimer.current);
          finalizeTimer.current = setInterval(() => {
            const finElapsed = (Date.now() - finalizeStart) / 1000;
            const finFrac = Math.min(finElapsed / estFinalizeSec, 0.99);
            advance(((actualSendSec + finFrac * estFinalizeSec) / total) * 100);
            const remaining = estFinalizeSec - finElapsed;
            // Only call it "Finalizing…" once the estimated R2 upload has run
            // its full course — until then it's still uploading, with a countdown.
            setStatusText(remaining > 0 ? fmtRemaining(remaining) : "Finalizing…");
          }, 500);
        }
      });
      if (finalizeTimer.current) clearInterval(finalizeTimer.current);
      setProgress(100); // server confirmed — snap the bar to done
      // Write the new game straight into the cache so the Library shows it
      // immediately — invalidating alone let a prefetch that started during
      // the (long) upload resolve afterwards and repopulate the cache with a
      // stale list that omitted this game (CF-63).
      addGameToCache(game);
      router.push(`/games/${game.id}`);
    } catch (e) {
      if (finalizeTimer.current) clearInterval(finalizeTimer.current);
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
          accept={ACCEPTED.join(",")}
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
              MP4, MOV, AVI, WebM · up to {MAX_SIZE_GB} GB
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
