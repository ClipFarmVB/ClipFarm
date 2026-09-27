import type { CSSProperties } from "react";
import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { DemoVideo } from "@/components/landing/DemoVideo";
import { MockClipCard, type MockClip } from "@/components/landing/MockClipCard";
import { SampleOutput } from "@/components/landing/SampleOutput";
import { SignedInRedirect } from "@/components/landing/SignedInRedirect";

// The landing page for signed-out visitors (CF-219). Signed-in users never see
// it: the middleware sends them to /games, and SignedInRedirect covers a
// client-side navigation served from the router cache.

// No image here — the social card is CF-248's. This title replaces the root
// layout's rather than extending it, as the layout sets no template.
export const metadata: Metadata = {
  title: "ClipFarm — Volleyball highlights from full-game footage",
  description:
    "Upload a full volleyball game and get the rallies worth keeping cut into clips, tagged by action and ranked by how big the play was.",
  openGraph: {
    title: "ClipFarm — Volleyball highlights from full-game footage",
    description:
      "Upload a full volleyball game and get the rallies worth keeping cut into clips, tagged by action and ranked by how big the play was.",
  },
};

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

// ─── C: Hero card stack ───────────────────────────────────────────
interface HeroClip extends MockClip {
  pos: CSSProperties;
  /** One of the `.float-*` classes in globals.css. */
  floatClass: string;
}

const HERO_CLIPS: HeroClip[] = [
  {
    action: "serve", confidence: 79, time: "02:11", duration: "0:08",
    thumbFrom: "from-sky-950",
    dotClass: "bg-sky-400", confClass: "text-amber-500",
    pos: { position: "absolute", top: 48, left: 32, zIndex: 10 },
    floatClass: "float-c",
  },
  {
    action: "dig", confidence: 87, time: "08:45", duration: "0:05",
    thumbFrom: "from-emerald-950",
    dotClass: "bg-emerald-400", confClass: "text-amber-500",
    pos: { position: "absolute", top: 24, left: 16, zIndex: 20 },
    floatClass: "float-b",
  },
  {
    action: "spike", confidence: 94, time: "14:23", duration: "0:06",
    thumbFrom: "from-red-950",
    dotClass: "bg-red-400", confClass: "text-emerald-500",
    pos: { position: "absolute", top: 0, left: 0, zIndex: 30 },
    floatClass: "float-a",
  },
];

// ─── Sample output ────────────────────────────────────────────────
// Illustrative mock-ups, labelled as such on the page. CF-220's sample game
// replaces these with real clips through the same SampleOutput props.
const SAMPLE_CLIPS: MockClip[] = [
  {
    action: "spike", confidence: 94, time: "14:23", duration: "0:06",
    thumbFrom: "from-red-950", dotClass: "bg-red-400", confClass: "text-emerald-500",
  },
  {
    action: "block", confidence: 88, time: "21:07", duration: "0:07",
    thumbFrom: "from-orange-950", dotClass: "bg-orange-400", confClass: "text-emerald-500",
  },
  {
    action: "dig", confidence: 87, time: "08:45", duration: "0:05",
    thumbFrom: "from-emerald-950", dotClass: "bg-emerald-400", confClass: "text-amber-500",
  },
  {
    action: "serve", confidence: 79, time: "02:11", duration: "0:08",
    thumbFrom: "from-sky-950", dotClass: "bg-sky-400", confClass: "text-amber-500",
  },
];

// ─── How it works ─────────────────────────────────────────────────
// Kept in step with the README's Key Concepts: ball tracking finds the play,
// pose only refines the label. Describing pose as the detector is the mistake
// this copy used to make.
const STEPS = [
  {
    step: "01",
    title: "Upload the full game",
    body: "Drop in the whole recording as MP4, MOV, MKV, or WebM. No trimming first — processing runs in the background.",
  },
  {
    step: "02",
    title: "Rallies found, the best kept",
    body: "Ball tracking follows the play to find each rally, then scores it on crowd reaction and rally shape and keeps the best.",
  },
  {
    step: "03",
    title: "Each play labelled",
    body: "Clips are tagged spike, serve, dig, set, or block from the ball's path, with pose estimation refining the call.",
  },
  {
    step: "04",
    title: "Browse and download",
    body: "Filter by action, confidence, or top plays. Every clip is trimmed and ready to download.",
  },
];

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-6 text-[11px] font-semibold uppercase tracking-widest text-subtle">
      {children}
    </p>
  );
}

function SignUpCtas() {
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

// ─── Page ─────────────────────────────────────────────────────────
export default function HomePage() {
  return (
    <div>
      <SignedInRedirect />

      {/* ── A: Scrolling ticker ─────────────────────────────────── */}
      <div className="-mx-4 overflow-hidden border-b border-border/40 sm:-mx-6 lg:-mx-8" aria-hidden>
        <div className="ticker">
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
      {/* No overflow-hidden here — it would clip the card stack shadows, and
          nothing needs clipping: the negative margins below exactly cancel the
          page container's gutters at every breakpoint, so the bleed reaches the
          viewport edge and stops. (An earlier comment here credited a page-root
          `overflow-x-hidden`; there has never been one.) */}
      <div className="relative -mx-4 sm:-mx-6 lg:-mx-8">
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

        <div className="relative px-4 py-14 sm:px-6 sm:py-18 lg:px-8">
          <div className="grid grid-cols-1 gap-12 lg:grid-cols-[1fr_auto] lg:items-center lg:gap-20">

            {/* Left: eyebrow + headline (B) + subtitle + CTA */}
            <div className="max-w-[500px]">
              <p
                className="landing-fade mb-5 text-[11px] font-semibold uppercase tracking-widest text-muted"
                style={{ animationDelay: "0.1s" }}
              >
                Volleyball highlights
              </p>

              {/* B: Word-by-word reveal */}
              <h1 className="text-[36px] sm:text-[48px] font-semibold tracking-tight text-foreground leading-[1.12]">
                {HEADLINE.map((word, i) => (
                  <span
                    key={i}
                    className="inline-block overflow-hidden align-bottom"
                    style={{ marginRight: "0.22em" }}
                  >
                    <span
                      className="word-up"
                      style={{ animationDelay: `${120 + i * 70}ms` }}
                    >
                      {word}
                    </span>
                  </span>
                ))}
              </h1>

              <p
                className="landing-fade mt-5 text-[14px] text-muted leading-[1.75] max-w-[400px]"
                style={{ animationDelay: "0.75s" }}
              >
                Upload a full game and get back a filterable feed of its best rallies —
                the spikes, serves, digs, sets, and blocks worth keeping, each in its own clip.
              </p>

              <div className="landing-fade mt-8" style={{ animationDelay: "0.9s" }}>
                <SignUpCtas />
              </div>
            </div>

            {/* Right: C — mock clip card stack */}
            <div
              className="landing-fade relative hidden lg:block shrink-0"
              style={{ width: 250, height: 260, animationDelay: "0.4s" }}
              aria-hidden
            >
              {HERO_CLIPS.map(({ pos, floatClass, ...clip }) => (
                <div key={clip.action} style={pos}>
                  <div className={floatClass}>
                    <MockClipCard {...clip} />
                  </div>
                </div>
              ))}
            </div>

          </div>
        </div>
      </div>

      {/* ── Demo video ──────────────────────────────────────────── */}
      <section className="py-12" aria-labelledby="landing-demo">
        <h2 id="landing-demo" className="sr-only">Demo video</h2>
        <SectionLabel>See it run</SectionLabel>
        <DemoVideo />
      </section>

      <div className="h-px bg-border" />

      {/* ── How it works ────────────────────────────────────────── */}
      <section className="py-12" aria-labelledby="landing-how">
        <h2 id="landing-how" className="sr-only">How it works</h2>
        <SectionLabel>How it works</SectionLabel>
        <div className="grid grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-4 stagger">
          {STEPS.map(({ step, title, body }) => (
            <div key={step}>
              <p className="mb-3 text-[11px] font-semibold tabular-nums text-brand">
                {step}
              </p>
              <h3 className="text-[13px] font-semibold text-foreground">{title}</h3>
              <p className="mt-1.5 text-[13px] text-muted leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </section>

      <div className="h-px bg-border" />

      {/* ── Sample output ───────────────────────────────────────── */}
      <section className="py-12" aria-labelledby="landing-sample">
        <h2 id="landing-sample" className="sr-only">What you get</h2>
        <SectionLabel>What you get</SectionLabel>
        <SampleOutput
          clips={SAMPLE_CLIPS}
          label="Example clips, for illustration — not from a real game."
        />
      </section>

      {/* ── Closing sign-up band ────────────────────────────────── */}
      <section
        className="mb-4 rounded-xl border border-border bg-surface px-6 py-10 sm:px-10"
        aria-labelledby="landing-cta"
      >
        <h2
          id="landing-cta"
          className="text-[22px] sm:text-[26px] font-semibold tracking-tight text-foreground"
        >
          Turn your next game into highlights.
        </h2>
        <p className="mt-2 max-w-[440px] text-[14px] text-muted leading-relaxed">
          Create an account, upload a game, and come back to its clips.
        </p>
        <div className="mt-6">
          <SignUpCtas />
        </div>
      </section>

    </div>
  );
}
