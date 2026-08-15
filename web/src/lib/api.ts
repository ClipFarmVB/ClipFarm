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
  // A 204 has no body, so res.json() rejects with "Unexpected end of JSON
  // input" — on *success*. Every DELETE here returns 204, which is why
  // deleteGame hand-rolls its own fetch to sidestep this helper. Handled here
  // instead so the next DELETE doesn't have to.
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

// ─── Games ────────────────────────────────────────────────────────────────────

export interface Game {
  id: string;
  title: string;
  status: "queued" | "processing" | "ready" | "failed";
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

// Upload happens in two legs: the browser sends the file to the API
// ("sending", observable via XHR), then the API streams it up to R2 before
// responding ("finalizing", invisible to the browser — no progress events).
export type UploadProgress =
  | { phase: "sending"; loaded: number; total: number }
  | { phase: "finalizing"; total: number };

export async function uploadGame(
  file: File,
  title: string,
  condense: boolean = false,
  onProgress?: (p: UploadProgress) => void
): Promise<Game> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("title", title);
  formData.append("condense", String(condense));

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
        if (e.lengthComputable) onProgress({ phase: "sending", loaded: e.loaded, total: e.total });
      };
      // Body fully sent → the server is now uploading to R2 (the invisible leg).
      xhr.upload.onload = () => onProgress({ phase: "finalizing", total: file.size });
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

// ─── Profiles (CF-107) ────────────────────────────────────────────────────────

export interface Profile {
  id: string;
  username: string | null;
  display_name: string | null;
  bio: string | null;
  avatar_url: string | null;
  is_private: boolean;
  created_at: string;
}

/** The caller's own profile — adds fields not exposed on a public lookup. */
export interface Me extends Profile {
  email: string;
  username_changed_at: string | null;
  /** True while the handle is the one migration 010 generated, not one chosen. */
  username_is_generated: boolean;
}

export interface HandleAvailability {
  username: string;
  available: boolean;
  reason: string | null;
}

export function getMe(): Promise<Me> {
  return request<Me>("/users/me");
}

export function getProfile(handle: string): Promise<Profile> {
  return request<Profile>(`/users/${encodeURIComponent(handle)}`);
}

export function checkHandle(username: string): Promise<HandleAvailability> {
  return request<HandleAvailability>(
    `/users/handle-available?username=${encodeURIComponent(username)}`,
  );
}

export interface ProfileUpdate {
  username?: string;
  display_name?: string;
  bio?: string;
  is_private?: boolean;
}

export function updateMe(body: ProfileUpdate): Promise<Me> {
  return request<Me>("/users/me", { method: "PATCH", body: JSON.stringify(body) });
}

export async function uploadAvatar(file: File): Promise<Me> {
  const authHeaders = await getAuthHeaders();
  const form = new FormData();
  form.append("file", file);
  // No Content-Type header: the browser must set the multipart boundary itself.
  const res = await fetch(`${API_URL}/users/me/avatar`, {
    method: "POST",
    headers: authHeaders,
    body: form,
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  return res.json() as Promise<Me>;
}

// ─── Posts (CF-109) ───────────────────────────────────────────────────────────

export type Visibility = "private" | "followers" | "public";

export interface PostPlayback {
  clip_url: string | null;
  thumbnail_url: string | null;
  proxy_url: string | null;
  start_time: number;
  end_time: number;
}

export interface Post {
  id: string;
  clip_id: string;
  caption: string | null;
  visibility: Visibility;
  like_count: number;
  comment_count: number;
  created_at: string;
  author: {
    id: string;
    username: string | null;
    display_name: string | null;
    avatar_url: string | null;
  };
  playback: PostPlayback;
}

export function createPost(
  clipId: string,
  caption: string,
  visibility: Visibility,
): Promise<Post> {
  return request<Post>("/posts", {
    method: "POST",
    body: JSON.stringify({ clip_id: clipId, caption, visibility }),
  });
}

export function getUserPosts(username: string): Promise<Post[]> {
  return request<Post[]>(`/posts?username=${encodeURIComponent(username)}`);
}

export function deletePost(postId: string): Promise<void> {
  return request<void>(`/posts/${postId}`, { method: "DELETE" });
}
