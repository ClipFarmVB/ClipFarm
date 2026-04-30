"use client";

import { useAuth } from "@/contexts/AuthContext";

/**
 * Shows a loading spinner while the Supabase session is resolving.
 * Route protection itself is handled by src/middleware.ts — this component
 * just prevents a flash of unauthenticated content on the client.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="h-6 w-6 rounded-full border-2 border-border-strong border-t-brand animate-spin" />
      </div>
    );
  }

  return <>{children}</>;
}
