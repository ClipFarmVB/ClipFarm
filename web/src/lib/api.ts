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
  // "uploading" means the row exists but the browser is still PUTting the
  // video to R2. The Library filters these out server-side, so they're only
  // ever seen by the tab doing the upload.
  status: "uploading" | "queued" | "processing" | "ready" | "failed";
  progress: number;
  progress_stage: string | null;
  created_at: string;
  clip_count?: number;
  condense_requested?: boolean;
  condensed_video_url?: string | null;
  original_duration?: number | null;
  condensed_duration?: number | null;
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

// ─── Uploads ─────────────────────────────────────────────────────────────────
// The video goes browser → R2 directly (CF-163). The api only issues a ticket
// of presigned URLs and confirms the object afterwards; it never sees the
// bytes. The transfer itself lives in ./upload.

/** Server-owned limits. The client renders and validates against these rather
 *  than its own constants, so the advertised cap is always the enforced one. */
export interface UploadConfig {
  max_upload_bytes: number;
  allowed_content_types: string[];
  single_put_max_bytes: number;
  part_size_bytes: number;
  url_ttl_seconds: number;
}

export interface UploadPart {
  part_number: number;
  url: string;
}

/** Everything needed to upload without talking to the api again until done. */
export interface UploadTicket {
  game_id: string;
  mode: "single" | "multipart";
  content_type: string;
  expires_in: number;
  upload_url: string | null;
  upload_id: string | null;
  part_size_bytes: number | null;
  parts: UploadPart[];
}

export interface CompletedPart {
  part_number: number;
  etag: string;
}

export function getUploadConfig(): Promise<UploadConfig> {
  return request<UploadConfig>("/games/upload-config");
}

export function createUpload(input: {
  title: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  condense: boolean;
}): Promise<UploadTicket> {
  return request<UploadTicket>("/games/uploads", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Confirm the object is in R2 so the api can queue processing. Only after
 *  this resolves does the game exist as far as the rest of the app is
 *  concerned — a failed transfer never becomes a job. */
export function completeUpload(gameId: string, parts: CompletedPart[]): Promise<Game> {
  return request<Game>(`/games/${gameId}/uploads/complete`, {
    method: "POST",
    body: JSON.stringify({ parts }),
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
  highlight_score: number | null;
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
  min_score?: number;
  sort?: "time" | "score";
  page?: number;
  page_size?: number;
}

export function getClips(gameId: string, filters: ClipFilters = {}): Promise<Clip[]> {
  const params = new URLSearchParams();
  if (filters.action_type?.length) params.set("action_type", filters.action_type.join(","));
  if (filters.player_id) params.set("player_id", filters.player_id);
  if (filters.min_confidence != null) params.set("min_confidence", String(filters.min_confidence));
  if (filters.min_score != null && filters.min_score > 0) params.set("min_score", String(filters.min_score));
  if (filters.sort) params.set("sort", filters.sort);
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
