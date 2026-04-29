import type { CSSProperties } from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { StatCounter } from "@/components/StatCounter";

// ─── A: Ticker data ───────────────────────────────────────────────
const ACTIONS = [
  { label: "spike",  dot: "bg-red-400"     },
  { label: "serve",  dot: "bg-sky-400"     },
  { label: "dig",    dot: "bg-emerald-400" },
  { label: "set",    dot: "bg-violet-400"  },
  { label: "block",  dot: "bg-orange-400"  },
];
// Two copies × two passes = 4 full sets, always fills the viewport width.
// Animation moves by -50% (= one full double-set), creating a seamless loop.
const TICKER = [...ACTIONS, ...ACTIONS, ...ACTIONS, ...ACTIONS];

// ─── B: Headline words ────────────────────────────────────────────
const HEADLINE = [
  "From", "full", "game", "to",
  "highlight", "reel,", "automatically.",
];

// ─── C: Mock clip cards ───────────────────────────────────────────
interface MockClipData {
  action: string;
  confidence: number;
  time: string;
  duration: string;
  thumbFrom: string;
  dotClass: string;
  confClass: string;
  pos: CSSProperties;
  anim: string;
}

const MOCK_CLIPS: MockClipData[] = [
  {
    action: "serve", confidence: 79, time: "02:11", duration: "0:08",
    thumbFrom: "from-sky-950",
    dotClass: "bg-sky-400", confClass: "text-amber-400",
    pos: { position: "absolute", top: 14, left: 9, zIndex: 10 },
    anim: "float-c 4.7s ease-in-out infinite 1.4s",
  },
  {
    action: "dig", confidence: 87, time: "08:45", duration: "0:05",
    thumbFrom: "from-emerald-950",
    dotClass: "bg-emerald-400", confClass: "text-amber-400",
    pos: { position: "absolute", top: 7, left: 5, zIndex: 20 },
    anim: "float-b 5.1s ease-in-out infinite 0.7s",
  },
  {
    action: "spike", confidence: 94, time: "14:23", duration: "0:06",
    thumbFrom: "from-red-950",
    dotClass: "bg-red-400", confClass: "text-emerald-400",
    pos: { position: "absolute", top: 0, left: 0, zIndex: 30 },
    anim: "float-a 4.2s ease-in-out infinite",
  },
];

// ─── How it works ─────────────────────────────────────────────────
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

// ─── MockClipCard ─────────────────────────────────────────────────
function MockClipCard({
  action, confidence, time, duration, thumbFrom, dotClass, confClass,
}: Omit<MockClipData, "pos" | "anim">) {
  return (
    <div className="w-[210px] overflow-hidden rounded-xl border border-border bg-surface shadow-2xl shadow-black/60 select-none">
      {/* Thumbnail */}
      <div className={`relative aspect-video bg-gradient-to-br ${thumbFrom}/30 to-surface-high flex items-center justify-center`}>
        {/* Action badge */}
        <div className="absolute top-2 left-2 flex items-center gap-1.5 rounded bg-black/50 backdrop-blur-sm px-2 py-0.5">
          <span className={`h-1 w-1 rounded-full shrink-0 ${dotClass}`} />
          <span className="text-[10px] font-semibold uppercase tracking-widest text-white/70">
            {action}
          </span>
        </div>
        {/* Play button */}
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-white/8 border border-white/12">
          <svg className="ml-0.5 h-3.5 w-3.5 fill-white/50" viewBox="0 0 24 24">
            <path d="M8 5v14l11-7z" />
          </svg>
        </div>
        {/* Duration */}
        <div className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white/75 tabular-nums">
          {duration}
        </div>
      </div>
      {/* Footer */}
      <div className="flex items-center justify-between px-2.5 py-2 border-t border-border">
        <span className={`text-[11px] font-semibold tabular-nums ${confClass}`}>
          {confidence}%
        </span>
        <span className="text-[10px] text-subtle tabular-nums">{time}</span>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────
export default function HomePage() {
  return (
    <div className="overflow-x-hidden">

      {/* ── A: Scrolling ticker ─────────────────────────────────── */}
      <div className="-mx-8 overflow-hidden border-b border-border/40">
        <div
          style={{
            display: "flex",
            width: "max-content",
            animation: "ticker 28s linear infinite",
          }}
        >
          {TICKER.map((item, i) => (
            <div
              key={i}
              className="flex items-center gap-2 px-6 py-2.5 whitespace-nowrap border-r border-border/40"
            >
              <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${item.dot}`} />
              <span className="text-[11px] font-medium text-subtle">{item.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Hero: B + C + E ─────────────────────────────────────── */}
      {/* No overflow-hidden here — it would clip the card stack shadows. */}
      {/* Horizontal scroll is already suppressed by the page root overflow-x-hidden. */}
      <div className="-mx-8 relative">
        {/* E: Dot grid — drifts diagonally */}
        <div className="dot-grid absolute inset-0 pointer-events-none" />
        {/* Fade only at the very bottom so the grid stays visible */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              "linear-gradient(to bottom, transparent 55%, var(--cf-bg) 100%)",
          }}
        />

        <div className="relative px-8 py-18">
          <div className="grid grid-cols-1 gap-12 lg:grid-cols-[1fr_auto] lg:items-center lg:gap-20">

            {/* Left: eyebrow + headline (B) + subtitle + CTA */}
            <div className="max-w-[500px]">
              <p
                className="mb-5 text-[11px] font-semibold uppercase tracking-widest text-muted"
                style={{ animation: "fade-up 0.4s cubic-bezier(0.16,1,0.3,1) 0.1s both" }}
              >
                Volleyball highlights
              </p>

              {/* B: Word-by-word reveal */}
              <h1 className="text-[40px] sm:text-[48px] font-semibold tracking-tight text-foreground leading-[1.12]">
                {HEADLINE.map((word, i) => (
                  <span
                    key={i}
                    className="inline-block overflow-hidden align-bottom"
                    style={{ marginRight: "0.22em" }}
                  >
                    <span
                      className="inline-block"
                      style={{
                        animation: "word-up 0.65s cubic-bezier(0.16,1,0.3,1) both",
                        animationDelay: `${120 + i * 70}ms`,
                      }}
                    >
                      {word}
                    </span>
                  </span>
                ))}
              </h1>

              <p
                className="mt-5 text-[14px] text-muted leading-[1.75] max-w-[380px]"
                style={{ animation: "fade-up 0.5s cubic-bezier(0.16,1,0.3,1) 0.75s both" }}
              >
                Upload game footage and get a filterable feed of every spike,
                serve, dig, set, and block — automatically tagged by player.
              </p>

              <div
                className="mt-8 flex items-center gap-3"
                style={{ animation: "fade-up 0.5s cubic-bezier(0.16,1,0.3,1) 0.9s both" }}
              >
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

            {/* Right: C — mock clip card stack */}
            <div
              className="relative hidden lg:block shrink-0"
              style={{
                width: 230,
                height: 200,
                animation: "fade-up 0.8s cubic-bezier(0.16,1,0.3,1) 0.4s both",
              }}
            >
              {MOCK_CLIPS.map((clip) => (
                <div key={clip.action} style={clip.pos}>
                  <div style={{ animation: clip.anim }}>
                    <MockClipCard {...clip} />
                  </div>
                </div>
              ))}
            </div>

          </div>
        </div>
      </div>

      {/* ── Divider ─────────────────────────────────────────────── */}
      <div className="h-px bg-border" />

      {/* ── D: Stat counters ────────────────────────────────────── */}
      <StatCounter />

      {/* ── Divider ─────────────────────────────────────────────── */}
      <div className="h-px bg-border" />

      {/* ── How it works ────────────────────────────────────────── */}
      <div className="py-12">
        <p className="mb-8 text-[11px] font-semibold uppercase tracking-widest text-subtle">
          How it works
        </p>
        <div className="grid grid-cols-1 gap-8 sm:grid-cols-3 stagger">
          {STEPS.map(({ step, title, body }) => (
            <div key={step}>
              <p className="mb-3 text-[11px] font-semibold tabular-nums text-brand/60">
                {step}
              </p>
              <h3 className="text-[13px] font-semibold text-foreground">{title}</h3>
              <p className="mt-1.5 text-[13px] text-muted leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </div>

    </div>
  );
}
