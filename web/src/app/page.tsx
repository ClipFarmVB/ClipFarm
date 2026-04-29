import Link from "next/link";
import { ArrowRight, Clapperboard, SlidersHorizontal, Users, Zap } from "lucide-react";
import { Button } from "@/components/ui/Button";

const STEPS = [
  {
    step: "01",
    title: "Upload footage",
    body: "Drop any MP4, MOV, or AVI up to 15 GB. Processing starts immediately in the background.",
  },
  {
    step: "02",
    title: "AI detects actions",
    body: "YOLOv8 pose estimation identifies spikes, serves, digs, sets, and blocks frame-by-frame.",
  },
  {
    step: "03",
    title: "Browse your clips",
    body: "Filter by action type, player, or confidence. Every clip is trimmed and ready to share.",
  },
];

const CAPABILITIES = [
  { icon: Clapperboard, label: "Auto clip cutting" },
  { icon: Zap,          label: "Pose estimation" },
  { icon: SlidersHorizontal, label: "Filter by action" },
  { icon: Users,        label: "Player tagging" },
];

export default function HomePage() {
  return (
    <div className="fade-up">
      {/* Hero */}
      <div className="pt-8 pb-16 max-w-xl">
        <p className="mb-4 text-[11px] font-semibold uppercase tracking-widest text-muted">
          Volleyball highlights
        </p>
        <h1 className="text-3xl font-semibold tracking-tight text-foreground leading-[1.2]">
          From full game to
          <br />
          highlight reel automatically
        </h1>
        <p className="mt-4 text-[14px] text-muted leading-relaxed max-w-sm">
          Upload game footage and get a filterable feed of every spike, serve,
          dig, set, and block — tagged by player.
        </p>
        <div className="mt-7 flex items-center gap-3">
          <Link href="/upload">
            <Button size="lg">
              Upload a game
              <ArrowRight size={13} />
            </Button>
          </Link>
          <Link href="/games">
            <Button size="lg" variant="ghost">
              View library
            </Button>
          </Link>
        </div>
      </div>

      {/* Divider */}
      <div className="h-px bg-border" />

      {/* How it works */}
      <div className="py-12">
        <p className="mb-8 text-[11px] font-semibold uppercase tracking-widest text-subtle">
          How it works
        </p>
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-3 stagger">
          {STEPS.map(({ step, title, body }) => (
            <div key={step}>
              <p className="mb-3 text-[11px] font-semibold tabular-nums text-brand/70">{step}</p>
              <h3 className="text-[13px] font-semibold text-foreground">{title}</h3>
              <p className="mt-1.5 text-[13px] text-muted leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Divider */}
      <div className="h-px bg-border" />

      {/* Capabilities pill row */}
      <div className="py-10 flex flex-wrap gap-2">
        {CAPABILITIES.map(({ icon: Icon, label }) => (
          <div
            key={label}
            className="inline-flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-1.5 text-[12px] text-muted"
          >
            <Icon size={12} className="text-subtle" />
            {label}
          </div>
        ))}
        <div className="inline-flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-1.5 text-[12px] text-muted">
          + more
        </div>
      </div>
    </div>
  );
}
