"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Clapperboard, Upload, LayoutGrid, LogOut, Menu, Sun, Moon, FolderOpen, X } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useTheme } from "@/contexts/ThemeContext";
import { SOCIAL_ENABLED } from "@/lib/features";
import { useBodyScrollLockBelowLg } from "@/lib/useBodyScrollLock";
import { useIsDesktopLayout } from "@/lib/useIsDesktopLayout";
import { clearMe, needsHandle, useMe } from "@/lib/useMe";
import { cn } from "@/lib/utils";

// Tab order inside the drawer, for the wrap-around below. Deliberately the
// plain set: everything focusable here is a link or a button.
const FOCUSABLE =
  'a[href], button:not(:disabled), [tabindex]:not([tabindex="-1"])';

const NAV_ITEMS = [
  { href: "/games",       label: "Library",     icon: LayoutGrid },
  { href: "/collections", label: "Collections", icon: FolderOpen },
  { href: "/upload",      label: "Upload",      icon: Upload },
] as const;

export function Sidebar() {
  const pathname = usePathname();
  // Below lg the sidebar is an off-canvas drawer; from lg it is a permanent
  // column and `open` stops mattering — the lg: classes pin it open.
  const [open, setOpen] = useState(false);
  const asideRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  // Everything that navigates from inside the drawer closes it on the way
  // out, so it never sits over the page it just opened. Closing on `pathname`
  // instead would be the cascading-render pattern React warns about, and
  // would miss a tap on the link for the route you are already on.
  const closeOnNavigate = () => setOpen(false);
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

  // Only while it is actually an overlay — the hook's CSS drops the lock by
  // itself above `lg`, where the aside is a column and there is no backdrop.
  useBodyScrollLockBelowLg(open);

  // Everything a drawer covering the page owes the keyboard: Escape closes it,
  // focus moves in on open and back to the trigger on close, and Tab stays
  // inside rather than walking into the page behind the backdrop. None of it
  // applies from `lg`, where the aside is an ordinary column in the page.
  const isDesktop = useIsDesktopLayout();
  useEffect(() => {
    if (!open || isDesktop) return;
    const trigger = triggerRef.current;
    closeRef.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); return; }
      if (e.key !== "Tab") return;
      const aside = asideRef.current;
      if (!aside) return;
      const items = aside.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      // Focus outside the drawer wraps in *either* direction. It lands there
      // by clicking chrome inside the drawer that cannot hold focus — the
      // "Workspace" heading, the padding — which leaves it on <body>. Guarding
      // only the shift branch let a forward Tab from there fall through to the
      // hamburger behind the backdrop and walk on into <main>.
      const outside = !aside.contains(active);
      const leaving = e.shiftKey ? active === first : active === last;
      if (outside || leaving) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      // Only when the drawer actually closed. This cleanup also runs when the
      // viewport crosses into the desktop layout, and the trigger is display:
      // none there — focusing it would drop focus to the body instead.
      if (trigger?.isConnected && trigger.offsetParent !== null) trigger.focus();
    };
  }, [open, isDesktop]);

  return (
    <>
      {/* Mobile top bar — the only chrome visible until the drawer is opened.
          <main> reserves its 52px with padding, so it never covers content. */}
      <header className="fixed inset-x-0 top-0 z-20 flex h-[52px] items-center gap-1 border-b border-border bg-background px-2 lg:hidden">
        <button
          ref={triggerRef}
          onClick={() => setOpen(true)}
          aria-label="Open navigation"
          aria-expanded={open}
          aria-controls="app-sidebar"
          className="flex h-11 w-11 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface hover:text-foreground focus-ring"
        >
          <Menu size={18} />
        </button>
        <Link href="/" className="group flex items-center gap-2.5">
          <div className="flex h-[26px] w-[26px] items-center justify-center rounded-md bg-brand/10 transition-colors group-hover:bg-brand/20">
            <Clapperboard size={13} className="text-brand" strokeWidth={2.5} />
          </div>
          <span className="text-[14px] font-semibold tracking-tight text-foreground">
            ClipFarm
          </span>
        </Link>
      </header>

      {/* Backdrop — tap anywhere off the drawer to dismiss it */}
      {open && (
        <div
          onClick={() => setOpen(false)}
          aria-hidden
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
        />
      )}

      {/* `inert` when it is a closed overlay: translating it off-screen leaves
          every link in the tab order and in the accessibility tree, so a
          keyboard or screen-reader user walks through a drawer they cannot
          see. It must not be inert once it is the desktop column, which is
          why this one thing needs the breakpoint in JS. */}
      <aside
        ref={asideRef}
        id="app-sidebar"
        inert={!open && !isDesktop}
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-[264px] max-w-[82vw] flex-col bg-background border-r border-border",
          "transition-transform duration-200 ease-out lg:w-[220px] lg:max-w-none lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full"
        )}
      >
        {/* Logo */}
        <div className="flex h-[52px] shrink-0 items-center border-b border-border">
          <Link href="/" onClick={closeOnNavigate} className="group flex min-w-0 flex-1 items-center gap-2.5 px-4">
            <div className="flex h-[26px] w-[26px] items-center justify-center rounded-md bg-brand/10 transition-colors group-hover:bg-brand/20">
              <Clapperboard size={13} className="text-brand" strokeWidth={2.5} />
            </div>
            <span className="text-[14px] font-semibold tracking-tight text-foreground">
              ClipFarm
            </span>
          </Link>
          <button
            ref={closeRef}
            onClick={() => setOpen(false)}
            aria-label="Close navigation"
            className="mr-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface hover:text-foreground focus-ring lg:hidden"
          >
            <X size={16} />
          </button>
        </div>

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
                    onClick={closeOnNavigate}
                    className={cn(
                      "group flex items-center gap-2.5 rounded-md px-3 py-2.5 text-[13px] transition-all duration-150 lg:py-[7px]",
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
                onClick={closeOnNavigate}
                className="flex items-center gap-2.5 rounded-md px-3 py-2.5 text-[13px] text-muted hover:bg-surface hover:text-foreground transition-all duration-150 lg:py-[7px]"
              >
                Log in
              </Link>
              <Link
                href="/signup"
                onClick={closeOnNavigate}
                className="flex items-center gap-2.5 rounded-md px-3 py-2.5 text-[13px] text-muted hover:bg-surface hover:text-foreground transition-all duration-150 lg:py-[7px]"
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
            className="flex w-full items-center gap-2.5 rounded-md px-3 py-2.5 text-[13px] text-muted hover:bg-surface hover:text-foreground transition-all duration-150 lg:py-[7px]"
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
                  onClick={closeOnNavigate}
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
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded text-subtle hover:text-foreground hover:bg-surface-high transition-colors focus-ring lg:h-6 lg:w-6"
                title="Sign out"
                aria-label="Sign out"
              >
                <LogOut size={12} />
              </button>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
