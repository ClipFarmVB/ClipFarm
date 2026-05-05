import { createClient } from "@/lib/supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function getAuthHeaders(): Promise<Record<string, string>> {
  try {
    const supabase = createClient();
    const { data } = await supabase.auth.getSession();
    if (data.session?.access_token) {
      return { Authorization: `Bearer ${data.session.access_token}` };
    }
  } catch {
    // Not logged in — fall through
  }
  return {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders,
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ─── Games ────────────────────────────────────────────────────────────────────

export interface Game {
  id: string;
  title: string;
  status: "queued" | "processing" | "ready" | "failed";
  created_at: string;
  clip_count?: number;
}

export function getGames(): Promise<Game[]> {
  return request<Game[]>("/games");
}

export function getGame(id: string): Promise<Game> {
  return request<Game>(`/games/${id}`);
}

export function renameGame(id: string, title: string): Promise<Game> {
  return request<Game>(`/games/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export async function deleteGame(id: string): Promise<void> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${API_URL}/games/${id}`, {
    method: "DELETE",
    headers: authHeaders,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
}

export async function uploadGame(
  file: File,
  title: string,
  onProgress?: (pct: number) => void
): Promise<Game> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("title", title);

  const authHeaders = await getAuthHeaders();

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/games`);

    // Set auth header on XHR
    for (const [key, value] of Object.entries(authHeaders)) {
      xhr.setRequestHeader(key, value);
    }

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(`Upload failed: ${xhr.status} ${xhr.responseText}`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(formData);
  });
}

// ─── Clips ────────────────────────────────────────────────────────────────────

export type ActionType = "spike" | "serve" | "dig" | "set" | "block" | "unknown";

export interface Clip {
  id: string;
  game_id: string;
  player_id: string | null;
  player_name: string | null;
  action_type: ActionType;
  confidence: number;
  start_time: number;
  end_time: number;
  clip_url: string;
  thumbnail_url: string;
  labels: string[];
  created_at: string;
}

export interface ClipFilters {
  action_type?: ActionType[];
  player_id?: string;
  min_confidence?: number;
  page?: number;
  page_size?: number;
}

export function getClips(gameId: string, filters: ClipFilters = {}): Promise<Clip[]> {
  const params = new URLSearchParams();
  if (filters.action_type?.length) params.set("action_type", filters.action_type.join(","));
  if (filters.player_id) params.set("player_id", filters.player_id);
  if (filters.min_confidence != null) params.set("min_confidence", String(filters.min_confidence));
  if (filters.page) params.set("page", String(filters.page));
  if (filters.page_size) params.set("page_size", String(filters.page_size));
  const qs = params.toString();
  return request<Clip[]>(`/games/${gameId}/clips${qs ? `?${qs}` : ""}`);
}

export function getClipShareUrl(clipId: string): Promise<{ url: string }> {
  return request<{ url: string }>(`/clips/${clipId}/share`);
}

// ─── Players ──────────────────────────────────────────────────────────────────

export interface Player {
  id: string;
  name: string;
  jersey_number: number | null;
  team_id: string | null;
  photo_url: string | null;
}

export function getPlayers(teamId?: string): Promise<Player[]> {
  return request<Player[]>(`/players${teamId ? `?team_id=${teamId}` : ""}`);
}

export function tagClip(clipId: string, playerId: string): Promise<Clip> {
  return request<Clip>(`/clips/${clipId}/tag`, {
    method: "PATCH",
    body: JSON.stringify({ player_id: playerId }),
  });
}

export function updateClipLabels(clipId: string, labels: string[]): Promise<Clip> {
  return request<Clip>(`/clips/${clipId}/labels`, {
    method: "PATCH",
    body: JSON.stringify({ labels }),
  });
}

export function deleteClips(clipIds: string[]): Promise<{ deleted: number }> {
  return request<{ deleted: number }>(`/clips/delete`, {
    method: "POST",
    body: JSON.stringify({ clip_ids: clipIds }),
  });
}

export function trimClip(clipId: string, startDelta: number, endDelta: number): Promise<Clip> {
  return request<Clip>(`/clips/${clipId}/trim`, {
    method: "PATCH",
    body: JSON.stringify({ start_delta: startDelta, end_delta: endDelta }),
  });
}

// ─── Collections ─────────────────────────────────────────────────────────────

export interface Collection {
  id: string;
  name: string;
  clip_count: number;
  created_at: string;
}

export function getCollections(): Promise<Collection[]> {
  return request<Collection[]>("/collections");
}

export function createCollection(name: string): Promise<Collection> {
  return request<Collection>("/collections", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function renameCollection(id: string, name: string): Promise<Collection> {
  return request<Collection>(`/collections/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export function deleteCollection(id: string): Promise<void> {
  return request<void>(`/collections/${id}`, { method: "DELETE" });
}

export function getCollectionClips(id: string): Promise<Clip[]> {
  return request<Clip[]>(`/collections/${id}/clips`);
}

export function addClipToCollection(collectionId: string, clipId: string): Promise<void> {
  return request<void>(`/collections/${collectionId}/clips`, {
    method: "POST",
    body: JSON.stringify({ clip_id: clipId }),
  });
}

export function removeClipFromCollection(collectionId: string, clipId: string): Promise<void> {
  return request<void>(`/collections/${collectionId}/clips/${clipId}`, { method: "DELETE" });
}

// ─── Dead Time (temporary) ───────────────────────────────────────────────────

export interface DeadTimeRun {
  id: string;
  title: string;
  status: "queued" | "processing" | "ready" | "failed";
  created_at: string;
  clip_count?: number;
}

export interface DeadTimeClip {
  id: string;
  run_id: string;
  start_time: number;
  end_time: number;
  score: number;
  clip_url: string;
  thumbnail_url: string | null;
  created_at: string;
}

export function getDeadTimeRuns(): Promise<DeadTimeRun[]> {
  return request<DeadTimeRun[]>("/deadtime/runs");
}

export function getDeadTimeRun(runId: string): Promise<DeadTimeRun> {
  return request<DeadTimeRun>(`/deadtime/runs/${runId}`);
}

export function getDeadTimeClips(
  runId: string,
  page: number = 1,
  pageSize: number = 50
): Promise<DeadTimeClip[]> {
  return request<DeadTimeClip[]>(`/deadtime/runs/${runId}/clips?page=${page}&page_size=${pageSize}`);
}

export async function uploadDeadTimeRun(
  file: File,
  title: string,
  onProgress?: (pct: number) => void
): Promise<DeadTimeRun> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("title", title);

  const authHeaders = await getAuthHeaders();

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/deadtime/runs`);

    for (const [key, value] of Object.entries(authHeaders)) {
      xhr.setRequestHeader(key, value);
    }

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(`Upload failed: ${xhr.status} ${xhr.responseText}`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(formData);
  });
}
