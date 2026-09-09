import { apiErrorMessage } from "@/lib/apiError";
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

/**
 * Fail with the server's own explanation of a bad response.
 *
 * Shared because not every call can go through `request()` — a DELETE has no
 * JSON body, an avatar upload posts multipart — and those hand-rolled paths
 * are exactly the ones that drifted: an over-quota video upload reported a
 * clean sentence while the 2 MB avatar cap still showed
 * `API error 413: {"detail":"..."}`.
 */
async function throwApiError(res: Response): Promise<never> {
  const text = await res.text();
  throw new Error(apiErrorMessage(text, `API error ${res.status}: ${text}`));
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
  // Prefer the server's own explanation. The upload limits (CF-91) write
  // real, actionable sentences into `detail` — "you have 60 min of your
  // 360 min per 24 hours left" — which the raw form buried inside JSON.
  if (!res.ok) await throwApiError(res);
  // A 204 has no body, so res.json() rejects with "Unexpected end of JSON
  // input" — on *success*. Every DELETE here returns 204, and handling it here
  // is what lets them all share this helper: deleteGame used to hand-roll its
  // own fetch purely to sidestep the JSON parse.
  //
  // The content-length arm is deliberately wider than that: it catches *any*
  // empty body, including a 200 with none. Both are the same bug from the
  // caller's side — a `T` that was never sent — and returning undefined is
  // honest about it, where res.json() would throw a parse error that reads
  // like the response was malformed rather than absent.
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
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

export function deleteGame(id: string): Promise<void> {
  return request<void>(`/games/${id}`, { method: "DELETE" });
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

  // Per-user processing quota (CF-91). `*_remaining` is what's left in the
  // rolling window right now, so the allowance can be shown before a file is
  // even chosen rather than surfacing as a rejection at the end.
  max_duration_seconds: number;
  window_hours: number;
  max_games_per_window: number;
  games_used: number;
  games_remaining: number;
  max_minutes_per_window: number;
  minutes_used: number;
  minutes_remaining: number;
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
  // Read from the file in the browser. Lets the api reject an over-long video
  // and charge the quota before the transfer; the worker's probe settles it.
  duration_seconds?: number | null;
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
  // False once the game's raw upload has passed its retention window (CF-194):
  // the clip still plays, but it can no longer be re-cut, so trimming is off.
  source_available?: boolean;
  // The widest tier a post over this clip may take — the clip's own visibility
  // or its game's, resolved server-side (CF-109). The composer greys out
  // anything above it; without this the only way to learn the ceiling was to
  // submit and read the 409, and since nothing can raise a clip's visibility
  // yet that was a dead end rather than a step.
  //
  // Optional, and absent means `private`: a response from a path that hasn't
  // been taught to resolve it offers less, never more.
  effective_visibility?: Visibility;
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

/**
 * A URL for the same clip that saves under a readable name (CF-100).
 *
 * Separate from getClipShareUrl because the two URLs differ: this one carries
 * Content-Disposition: attachment, which the share link must not — a shared
 * link is meant to play.
 *
 * Note the browser does the naming from that header, not from an <a download>
 * attribute: download is ignored for cross-origin URLs, and R2 is a different
 * origin. So the caller points the browser at this URL and lets the header do
 * the work — through lib/download.ts, which explains why that is a hidden frame
 * rather than window.location.
 */
export function getClipDownloadUrl(clipId: string): Promise<{ url: string }> {
  return request<{ url: string }>(`/clips/${clipId}/download`);
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
  if (!res.ok) await throwApiError(res);
  return res.json() as Promise<Me>;
}

// ─── Posts (CF-109) ───────────────────────────────────────────────────────────

export type Visibility = "private" | "followers" | "public";

export interface PostPlayback {
  clip_url: string | null;
  thumbnail_url: string | null;
  /**
   * Per-game proxy (CF-48). Null on every post today, because neither CF-48 nor
   * the CF-51 virtual-clip player has landed — the feed prefers it when present
   * and seeks to (start_time, end_time), and falls back to the per-clip file
   * otherwise. That fallback is the only path currently exercised.
   */
  proxy_url: string | null;
  start_time: number;
  end_time: number;
  action_type: ActionType;
  highlight_score: number | null;
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
  viewer_has_liked: boolean;
}

/** One page of the home feed (CF-111). `next_cursor` is null on the last page. */
export interface FeedPage {
  items: Post[];
  next_cursor: string | null;
}

/**
 * Fetch a page of the home feed.
 *
 * The cursor is opaque and must be passed back verbatim — it encodes
 * `(created_at, id)` so paging can't duplicate or skip a post while new ones
 * are being inserted. Never build one client-side.
 */
export function getFeed(cursor?: string | null, limit = 20): Promise<FeedPage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return request<FeedPage>(`/feed?${params}`);
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

/**
 * Posts by one author — what a profile grid renders.
 *
 * Capped, not paged (CF-109). The caller passes the limit it intends to render
 * so it can tell a full page from a truncated one: getting exactly `limit` back
 * means there may be older posts it is not showing. That matters because the
 * profile is currently the only surface for *unpublishing*, so a silently
 * truncated list is a post the author cannot reach.
 */
export async function getUserPosts(username: string, limit = 50): Promise<Post[]> {
  const params = new URLSearchParams({ username, limit: String(limit) });
  // `?? []` because `request` returns `undefined as T` for an empty body — a
  // 204, or a 200 with none. That is right for the DELETEs it was added for and
  // a lie for a list: the caller is handed `undefined` typed as `Post[]`, and
  // the first `.length` throws a TypeError somewhere far from here. PostGrid's
  // `posts === null` loading guard does not catch it either, since undefined is
  // not null, so the failure renders as a blank page rather than as its error
  // card. An absent body means no posts; say so here, where the shape is known.
  return (await request<Post[]>(`/posts?${params}`)) ?? [];
}

export function deletePost(postId: string): Promise<void> {
  return request<void>(`/posts/${postId}`, { method: "DELETE" });
}
