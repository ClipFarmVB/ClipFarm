"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { getDeadTimeRuns, uploadDeadTimeRun, type DeadTimeRun } from "@/lib/api";

export default function DeadTimePage() {
  const [runs, setRuns] = useState<DeadTimeRun[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function loadRuns() {
      try {
        const data = await getDeadTimeRuns();
        if (!active) return;
        setRuns(data);
      } catch (e) {
        if (!active) return;
        setError(e instanceof Error ? e.message : "Failed to load dead-time runs.");
      } finally {
        if (active) setLoadingRuns(false);
      }
    }

    loadRuns();
    return () => {
      active = false;
    };
  }, []);

  const onPick = (next: File | null) => {
    setFile(next);
    if (next && !title) {
      setTitle(next.name.replace(/\.[^.]+$/, ""));
    }
  };

  const onUpload = async () => {
    if (!file) return;
    setError(null);
    setUploading(true);

    try {
      const created = await uploadDeadTimeRun(file, title || file.name, setProgress);
      setRuns((prev) => [created, ...prev]);
      setFile(null);
      setTitle("");
      setProgress(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed.");
      setProgress(null);
    } finally {
      setUploading(false);
    }
  };

  return (
    <RequireAuth>
      <div className="mx-auto max-w-5xl space-y-8">
        <section className="rounded-2xl border border-border bg-surface p-6">
          <h1 className="text-xl font-bold text-foreground">Dead Time (Temporary)</h1>
          <p className="mt-1 text-sm text-muted">
            Upload a game and run dead-time extraction independently from action detection.
          </p>

          <div className="mt-5 grid gap-3 md:grid-cols-[1fr,220px]">
            <input
              type="file"
              accept="video/*"
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
              className="rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
            <Button onClick={onUpload} disabled={!file || uploading}>
              {uploading ? "Uploading..." : "Upload & Process Dead Time"}
            </Button>
          </div>

          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Run title"
            className="mt-3 w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
          />

          {progress !== null && (
            <p className="mt-2 text-xs text-muted">Upload progress: {progress}%</p>
          )}

          {error && (
            <p className="mt-2 text-sm text-red-400">{error}</p>
          )}
        </section>

        <section>
          <h2 className="mb-3 text-lg font-semibold text-foreground">Runs</h2>
          {loadingRuns ? (
            <p className="text-sm text-muted">Loading dead-time runs...</p>
          ) : runs.length === 0 ? (
            <p className="text-sm text-muted">No runs yet.</p>
          ) : (
            <div className="grid gap-3">
              {runs.map((run) => (
                <Link
                  key={run.id}
                  href={`/deadtime/${run.id}`}
                  className="flex items-center justify-between rounded-xl border border-border bg-surface px-4 py-3 hover:border-border-strong"
                >
                  <div>
                    <p className="font-medium text-foreground">{run.title}</p>
                    <p className="text-xs text-muted">{new Date(run.created_at).toLocaleString()}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-xs uppercase tracking-wide text-muted">{run.status}</p>
                    <p className="text-xs text-muted">{run.clip_count ?? 0} clips</p>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>
    </RequireAuth>
  );
}
