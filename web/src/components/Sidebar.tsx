"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Clapperboard, Upload, LayoutGrid, Timer, LogOut } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/games",    label: "Library",    icon: LayoutGrid },
  { href: "/upload",   label: "Upload",     icon: Upload },
  { href: "/deadtime", label: "Dead Time",  icon: Timer },
] as const;

export function Sidebar() {
  const pathname = usePathname();
  const { user, loading, signOut } = useAuth();

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

      {/* User footer */}
      {user && (
        <div className="shrink-0 border-t border-border px-2 py-2">
          <div className="flex items-center gap-2.5 rounded-md px-2 py-2">
            {/* Avatar */}
            <div className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-surface-high border border-border text-[10px] font-bold uppercase text-muted">
              {user.email?.[0] ?? "?"}
            </div>
            {/* Email */}
            <span className="flex-1 min-w-0 truncate text-[11px] text-muted">
              {user.email}
            </span>
            {/* Sign out */}
            <button
              onClick={signOut}
              className="shrink-0 rounded p-1 text-subtle hover:text-foreground hover:bg-surface-high transition-colors focus-ring"
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut size={12} />
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
