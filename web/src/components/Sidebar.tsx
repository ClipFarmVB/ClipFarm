"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Clapperboard, Upload, LayoutGrid, LogOut, Sun, Moon, FolderOpen, Home } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useTheme } from "@/contexts/ThemeContext";
import { SOCIAL_ENABLED } from "@/lib/features";
import { clearMe, needsHandle, useMe } from "@/lib/useMe";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  // Feed first, and only with social on — it is the post-login landing page
  // (CF-112), so it should be the first thing in the nav that matches.
  ...(SOCIAL_ENABLED ? [{ href: "/feed", label: "Feed", icon: Home }] : []),
  { href: "/games",       label: "Library",     icon: LayoutGrid },
  { href: "/collections", label: "Collections", icon: FolderOpen },
  { href: "/upload",      label: "Upload",      icon: Upload },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user, loading, signOut } = useAuth();
  const { theme, toggle } = useTheme();
  // `enabled` false means no /users/me request at all — which is what keeps the
  // flag-off build from calling a route the API doesn't register.
  const me = useMe(SOCIAL_ENABLED && Boolean(user) && !loading);
  // A generated handle is not published — /users/{handle} 404s until it's
  // claimed — so linking to it would send the user to "No one is using @alice".
  // needsHandle() is the same predicate the banner and the API use.
  const hasPublicProfile = Boolean(me?.username) && !needsHandle(me);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href + "/");

  return (
    <aside className="fixed inset-y-0 left-0 z-40 flex w-[220px] flex-col bg-background border-r border-border">
      {/* Logo */}
      <Link
        href="/"
        className="flex h-[52px] shrink-0 items-center gap-2.5 px-4 border-b border-border group"
      >
        <div className="flex h-[26px] w-[26px] items-center justify-center rounded-md bg-brand/10 transition-colors group-hover:bg-brand/20">
          <Clapperboard size={13} className="text-brand" strokeWidth={2.5} />
        </div>
        <span className="text-[14px] font-semibold tracking-tight text-foreground">
          ClipFarm
        </span>
      </Link>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        {user && (
          <div className="space-y-0.5">
            <p className="px-3 pb-1.5 pt-0.5 text-[10px] font-semibold uppercase tracking-widest text-subtle">
              Workspace
            </p>
            {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
              const active = isActive(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={cn(
                    "group flex items-center gap-2.5 rounded-md px-3 py-[7px] text-[13px] transition-all duration-150",
                    active
                      ? "bg-surface-high text-foreground font-medium"
                      : "text-muted hover:bg-surface hover:text-foreground"
                  )}
                >
                  <Icon
                    size={14}
                    strokeWidth={active ? 2.5 : 2}
                    className={cn(
                      "shrink-0 transition-colors",
                      active ? "text-brand" : "text-subtle group-hover:text-muted"
                    )}
                  />
                  {label}
                  {active && (
                    <span className="ml-auto h-1.5 w-1.5 rounded-full bg-brand opacity-60" />
                  )}
                </Link>
              );
            })}
          </div>
        )}

        {!user && !loading && (
          <div className="space-y-0.5 pt-1">
            <Link
              href="/login"
              className="flex items-center gap-2.5 rounded-md px-3 py-[7px] text-[13px] text-muted hover:bg-surface hover:text-foreground transition-all duration-150"
            >
              Log in
            </Link>
            <Link
              href="/signup"
              className="flex items-center gap-2.5 rounded-md px-3 py-[7px] text-[13px] text-muted hover:bg-surface hover:text-foreground transition-all duration-150"
            >
              Sign up
            </Link>
          </div>
        )}
      </nav>

      {/* Footer — theme toggle + user */}
      <div className="shrink-0 border-t border-border px-2 py-2 space-y-1">
        {/* Theme toggle row */}
        <button
          onClick={toggle}
          className="flex w-full items-center gap-2.5 rounded-md px-3 py-[7px] text-[13px] text-muted hover:bg-surface hover:text-foreground transition-all duration-150"
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? (
            <Sun size={14} strokeWidth={2} className="shrink-0 text-subtle" />
          ) : (
            <Moon size={14} strokeWidth={2} className="shrink-0 text-subtle" />
          )}
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>

        {/* User row. With social on it's the entry point to your profile
            (CF-107) — the public page once a handle exists, the claim form
            until then. With the flag off it stays the pre-CF-107 row: avatar
            initial, email, sign out, and no request for /users/me. */}
        {user && (
          <div className="flex items-center gap-2.5 rounded-md px-2 py-2">
            {SOCIAL_ENABLED ? (
              <Link
                href={hasPublicProfile ? `/u/${me!.username}` : "/settings/profile"}
                className="flex min-w-0 flex-1 items-center gap-2.5 rounded focus-ring hover:opacity-80 transition-opacity"
                title={hasPublicProfile ? "View your profile" : "Set up your profile"}
              >
                {me?.avatar_url ? (
                  /* eslint-disable-next-line @next/next/no-img-element -- the R2
                     host isn't in next.config images.remotePatterns */
                  <img
                    src={me.avatar_url}
                    alt=""
                    className="h-[26px] w-[26px] shrink-0 rounded-full border border-border object-cover"
                  />
                ) : (
                  <div className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-surface-high border border-border text-[10px] font-bold uppercase text-muted">
                    {(hasPublicProfile ? me!.username! : user.email)?.[0] ?? "?"}
                  </div>
                )}
                <span className="flex-1 min-w-0 truncate text-[11px] text-muted">
                  {hasPublicProfile ? `@${me!.username}` : user.email}
                </span>
              </Link>
            ) : (
              <>
                <div className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-surface-high border border-border text-[10px] font-bold uppercase text-muted">
                  {user.email?.[0] ?? "?"}
                </div>
                <span className="flex-1 min-w-0 truncate text-[11px] text-muted">
                  {user.email}
                </span>
              </>
            )}
            {/* Sign out */}
            <button
              onClick={() => {
                // Drop the cached profile first so the next user never sees the
                // previous one's handle/avatar in the chrome.
                clearMe();
                void signOut();
              }}
              className="shrink-0 rounded p-1 text-subtle hover:text-foreground hover:bg-surface-high transition-colors focus-ring"
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut size={12} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
