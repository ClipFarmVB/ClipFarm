"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertCircle, Check, Loader2, Lock, Globe, Upload } from "lucide-react";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import {
  checkHandle,
  getMe,
  updateMe,
  uploadAvatar,
  type HandleAvailability,
  type Me,
} from "@/lib/api";
import { setMe as publishMe } from "@/lib/useMe";
import { cn } from "@/lib/utils";

function ProfileSettingsContent() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [bio, setBio] = useState("");
  const [isPrivate, setIsPrivate] = useState(true);
  const [saving, setSaving] = useState(false);

  const [availability, setAvailability] = useState<HandleAvailability | null>(null);
  const [checking, setChecking] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getMe()
      .then((data) => {
        setMe(data);
        setUsername(data.username ?? "");
        setDisplayName(data.display_name ?? "");
        setBio(data.bio ?? "");
        setIsPrivate(data.is_private);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  // Debounced availability check. Skipped when the handle is unchanged, so
  // editing only the bio doesn't render "that username is taken" against the
  // user's own handle.
  useEffect(() => {
    const trimmed = username.trim().toLowerCase();
    if (!trimmed || trimmed === (me?.username ?? "")) {
      setAvailability(null);
      return;
    }
    setChecking(true);
    const timer = setTimeout(() => {
      checkHandle(trimmed)
        .then(setAvailability)
        .catch(() => setAvailability(null))
        .finally(() => setChecking(false));
    }, 400);
    return () => {
      clearTimeout(timer);
      setChecking(false);
    };
  }, [username, me?.username]);

  const handleChanged = username.trim().toLowerCase() !== (me?.username ?? "");
  const blockedByHandle = handleChanged && availability?.available === false;

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await updateMe({
        // Only send the handle when it actually changed — the cooldown check
        // on the server treats any change as a rename.
        ...(handleChanged ? { username: username.trim().toLowerCase() } : {}),
        display_name: displayName,
        bio,
        is_private: isPrivate,
      });
      setMe(updated);
      publishMe(updated); // refresh the sidebar without a refetch
      setUsername(updated.username ?? "");
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save profile");
    } finally {
      setSaving(false);
    }
  }

  async function onAvatarPicked(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    try {
      const updated = await uploadAvatar(file);
      setMe(updated);
      publishMe(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Avatar upload failed");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  if (loading) {
    return <div className="text-sm text-muted">Loading profile…</div>;
  }

  const needsHandle = !me?.username;

  return (
    <div className="max-w-xl">
      <h1 className="text-2xl font-semibold tracking-tight">
        {needsHandle ? "Choose your username" : "Profile"}
      </h1>
      <p className="mt-1 text-sm text-muted">
        {needsHandle
          ? "Your username is how other players find you. You can change it later."
          : "This is what other people see."}
      </p>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="mt-6 space-y-5">
        {/* Avatar */}
        <div className="flex items-center gap-4">
          {/* eslint-disable-next-line @next/next/no-img-element -- R2 host isn't
              in next.config images.remotePatterns; a plain img avoids coupling
              this page to deploy-time image config. */}
          <img
            src={me?.avatar_url ?? "/favicon.ico"}
            alt=""
            className="h-16 w-16 rounded-full border border-border object-cover"
          />
          <div>
            <Button variant="secondary" size="sm" onClick={() => fileRef.current?.click()}>
              <Upload className="h-3.5 w-3.5" />
              Change photo
            </Button>
            <p className="mt-1 text-xs text-muted">JPG, PNG or WebP, up to 2 MB.</p>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="hidden"
            onChange={onAvatarPicked}
          />
        </div>

        {/* Username */}
        <label className="block">
          <span className="text-sm font-medium">Username</span>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-sm text-muted">@</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="yourname"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              className={cn(
                "w-full rounded-md border bg-surface-high px-3 py-2 text-sm",
                "focus-visible:outline-2 focus-visible:outline-brand",
                blockedByHandle ? "border-red-500/40" : "border-border",
              )}
            />
          </div>
          <p className="mt-1 min-h-[1.25rem] text-xs">
            {checking && <span className="text-muted">Checking…</span>}
            {!checking && blockedByHandle && (
              <span className="text-red-400">{availability?.reason}</span>
            )}
            {!checking && handleChanged && availability?.available && (
              <span className="text-brand">@{availability.username} is available</span>
            )}
            {!handleChanged && me?.username && (
              <span className="text-muted">
                Your profile is at{" "}
                <Link href={`/u/${me.username}`} className="underline">
                  /u/{me.username}
                </Link>
              </span>
            )}
          </p>
        </label>

        {/* Display name */}
        <label className="block">
          <span className="text-sm font-medium">Display name</span>
          <input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            maxLength={50}
            placeholder="How your name appears"
            className="mt-1 w-full rounded-md border border-border bg-surface-high px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-brand"
          />
        </label>

        {/* Bio */}
        <label className="block">
          <span className="text-sm font-medium">Bio</span>
          <textarea
            value={bio}
            onChange={(e) => setBio(e.target.value)}
            maxLength={280}
            rows={3}
            placeholder="Position, team, anything."
            className="mt-1 w-full resize-none rounded-md border border-border bg-surface-high px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-brand"
          />
          <span className="text-xs text-muted">{bio.length}/280</span>
        </label>

        {/* Privacy */}
        <button
          type="button"
          onClick={() => setIsPrivate(!isPrivate)}
          className="flex w-full items-start gap-3 rounded-md border border-border bg-surface-high px-3 py-3 text-left hover:border-border-strong"
        >
          {isPrivate ? (
            <Lock className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
          ) : (
            <Globe className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
          )}
          <span className="flex-1">
            <span className="block text-sm font-medium">
              {isPrivate ? "Private account" : "Public account"}
            </span>
            <span className="block text-xs text-muted">
              {isPrivate
                ? "Only followers you approve can see your clips. Recommended — game footage often shows minors."
                : "Anyone can see your clips, including people who aren't signed in."}
            </span>
          </span>
        </button>

        <div className="flex items-center gap-3">
          <Button onClick={save} disabled={saving || blockedByHandle || !username.trim()}>
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {needsHandle ? "Claim username" : "Save changes"}
          </Button>
          {saved && (
            <span className="flex items-center gap-1 text-sm text-brand">
              <Check className="h-4 w-4" /> Saved
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ProfileSettingsPage() {
  return (
    <RequireAuth>
      <ProfileSettingsContent />
    </RequireAuth>
  );
}
