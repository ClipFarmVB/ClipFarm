import Link from "next/link";
import { Upload } from "lucide-react";
import { LogoMark } from "@/components/LogoMark";
import { Button } from "@/components/ui/Button";
import { splitCredit } from "@/lib/credit";
import { cn } from "@/lib/utils";

/**
 * The demo game's markings (CF-565): the copy of the demo game a new account
 * starts with (CF-220, `is_sample`). Its footage is not the user's own, so the
 * UI says so wherever the game or its clips appear, and credits the footage
 * when the operator has set a credit.
 */

/** "Demo" pill, beside a game's title or on a demo clip. */
export function DemoBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded border border-brand/30 bg-brand-dim px-1.5 py-0.5",
        "text-[10px] font-semibold uppercase tracking-widest text-brand",
        className,
      )}
    >
      Demo
    </span>
  );
}

/**
 * The footage credit, as plain text with only an http(s) URL linked.
 * Renders nothing when there is no credit.
 */
export function DemoCredit({ credit, className }: { credit: string | null | undefined; className?: string }) {
  const parts = splitCredit(credit);
  if (parts.length === 0) return null;
  return (
    <p className={cn("text-[11px] text-muted", className)}>
      {parts.map((part, i) =>
        part.kind === "link" ? (
          <a
            key={i}
            href={part.href}
            target="_blank"
            rel="noopener noreferrer"
            className="break-all text-foreground underline decoration-border-strong underline-offset-2 hover:decoration-brand"
          >
            {part.text}
          </a>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </p>
  );
}

/** The banner at the top of a demo game's page. */
export function DemoBanner({ credit }: { credit: string | null | undefined }) {
  return (
    <section
      aria-label="About this demo game"
      className="mb-6 flex flex-col gap-4 rounded-lg border border-brand/25 bg-brand-dim p-4 sm:flex-row sm:items-center"
    >
      <LogoMark className="h-9 w-9" />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-foreground">This is a demo game</p>
        <p className="mt-0.5 text-[12px] text-muted">
          It shows what ClipFarm makes from a full game: every rally cut into its own clip,
          labelled by the play. Upload one of your games to get the same for it.
        </p>
        <DemoCredit credit={credit} className="mt-1.5" />
      </div>
      <Link href="/upload" className="shrink-0">
        <Button size="md">
          <Upload size={13} />
          Upload a game
        </Button>
      </Link>
    </section>
  );
}
