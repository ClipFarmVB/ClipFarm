"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, Film, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { uploadGame } from "@/lib/api";
import { cn } from "@/lib/utils";

const ACCEPTED = ["video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"];
const MAX_SIZE_GB = 15;

export function UploadZone() {
  const router = useRouter();
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) pickFile(f);
  };

  const onFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) pickFile(f);
  };

  const handleUpload = async () => {
    if (!file) return;
    setError(null);
    try {
      const game = await uploadGame(file, title || file.name, (pct) => setProgress(pct));
      router.push(`/games/${game.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
      setProgress(null);
    }
  };

  return (
    <div className="w-full max-w-lg">
      {/* Drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
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
        />

        {file ? (
          <>
            <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-brand/10 border border-brand/20">
              <Film size={18} className="text-brand" />
            </div>
            <p className="text-[14px] font-medium text-foreground">{file.name}</p>
            <p className="mt-1 text-[12px] text-muted">{formatBytes(file.size)}</p>
            <button
              onClick={() => document.getElementById("file-input")?.click()}
              className="mt-3 text-[11px] text-subtle hover:text-muted transition-colors"
            >
              Click to change file
            </button>
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
            placeholder="e.g. Varsity vs Lincoln — March 25"
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-[13px] text-foreground placeholder:text-subtle focus:border-border-strong focus:outline-none focus:ring-1 focus:ring-border-strong transition-colors"
          />
        </div>
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
            <span>Uploading…</span>
            <span className="tabular-nums">{progress}%</span>
          </div>
          <div className="h-0.5 rounded-full bg-surface-high overflow-hidden">
            <div
              className="h-full rounded-full bg-brand transition-all duration-300 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {/* Upload button */}
      {file && progress === null && (
        <Button className="mt-4 w-full" size="lg" onClick={handleUpload}>
          Upload &amp; process
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
