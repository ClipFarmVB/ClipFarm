"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/contexts/AuthContext";
import { DEFAULT_NEXT } from "@/lib/redirect";

/**
 * The landing page's calls to action (CF-574).
 *
 * `/` is open to everyone, signed in or not, and the brand logo links here for
 * both. A signed-in visitor gets one way back into the app instead of being
 * asked to sign up for an account they already have.
 *
 * While the session is still loading this renders the signed-out pair: it is
 * what the server rendered, so there is no layout shift for the common case
 * of a first-time visitor, and a signed-in user sees it swap once.
 */
export function LandingCtas() {
  const { user, loading } = useAuth();

  if (user && !loading) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Link href={DEFAULT_NEXT}>
          <Button size="lg">
            Go to your library
            <ArrowRight size={13} />
          </Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Link href="/signup">
        <Button size="lg">
          Get started
          <ArrowRight size={13} />
        </Button>
      </Link>
      <Link href="/login">
        <Button size="lg" variant="ghost">
          Log in
        </Button>
      </Link>
    </div>
  );
}
