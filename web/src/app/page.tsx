import type { CSSProperties } from "react";
import type { Metadata } from "next";
import { LogoMark } from "@/components/LogoMark";
import { MoltenBackdrop } from "@/components/MoltenBackdrop";
import { DemoVideo } from "@/components/landing/DemoVideo";
import { MockClipCard, type MockClip } from "@/components/landing/MockClipCard";
import { SampleOutput } from "@/components/landing/SampleOutput";
import { LandingCtas } from "@/components/landing/LandingCtas";

// The landing page (CF-219). Open to everyone since CF-574: the brand logo
// links here whether or not you are signed in, and LandingCtas swaps the
// sign-up buttons for a way back to the library when you are.

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
  /** Offsets and width as percentages of the stack, so the fan scales with it. */
  pos: CSSProperties;
  /** One of the `.float-*` classes in globals.css. */
  floatClass: string;
}

const HERO_CLIPS: HeroClip[] = [
  {
    action: "serve", confidence: 79, time: "02:11", duration: "0:08",
    thumbFrom: "from-sky-950",
    dotClass: "bg-sky-400", confClass: "text-amber-500",
    pos: { position: "absolute", top: "18%", left: "13%", width: "84%", zIndex: 10 },
    floatClass: "float-c",
  },
  {
    action: "dig", confidence: 87, time: "08:45", duration: "0:05",
    thumbFrom: "from-emerald-950",
    dotClass: "bg-emerald-400", confClass: "text-amber-500",
    pos: { position: "absolute", top: "9%", left: "6.5%", width: "84%", zIndex: 20 },
    floatClass: "float-b",
  },
  {
    action: "spike", confidence: 94, time: "14:23", duration: "0:06",
    thumbFrom: "from-red-950",
    dotClass: "bg-red-400", confClass: "text-emerald-500",
    pos: { position: "absolute", top: 0, left: 0, width: "84%", zIndex: 30 },
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

// Every section opens with a real, visible heading (CF-681). The small
// uppercase eyebrow stays above it as the section's running label; the h2 is
// what carries the hierarchy and what `aria-labelledby` points at.
function SectionHeading({
  id, eyebrow, children,
}: { id: string; eyebrow: string; children: React.ReactNode }) {
  return (
    <div className="mb-10">
      <p className="mb-3 text-ms-xs font-semibold uppercase tracking-widest text-subtle">
        {eyebrow}
      </p>
      <h2 id={id} className="text-ms-xl font-semibold tracking-tight text-foreground sm:text-ms-2xl">
        {children}
      </h2>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────
export default function HomePage() {
  return (
    <div>
      <MoltenBackdrop />

      {/* ── A: Scrolling ticker ─────────────────────────────────── */}
      <div className="-mx-4 overflow-hidden border-b border-border/40 sm:-mx-6 lg:-mx-8" aria-hidden>
        <div className="ticker">
          {TICKER.map((item, i) => (
            <div
              key={i}
              className="flex items-center gap-2 px-6 py-2.5 whitespace-nowrap border-r border-border/40"
            >
              <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${item.dot}`} />
              <span className="text-ms-xs font-medium text-subtle">{item.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Hero: B + C ─────────────────────────────────────────── */}
      {/* No overflow-hidden here — it would clip the card stack shadows, and
          nothing needs clipping: the negative margins below exactly cancel the
          page container's gutters at every breakpoint, so the bleed reaches the
          viewport edge and stops. (An earlier comment here credited a page-root
          `overflow-x-hidden`; there has never been one.) */}
      {/* E used to be a drifting dot grid here, with a fade to the page colour
          under it. The match-ball backdrop (CF-679) now textures the whole page,
          and a second texture on top of it was noise — and the opaque fade cut
          a hard edge across the backdrop's panels. */}
      <div className="relative -mx-4 sm:-mx-6 lg:-mx-8">
        <div className="relative px-4 py-16 sm:px-6 sm:py-20 lg:px-8">
          {/* Two columns from lg: the text takes the larger share and the card
              stack the rest, so the visual scales with the layout and weighs
              about as much as the headline block instead of sitting at a
              fixed 250px. */}
          <div className="grid grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)] lg:items-center lg:gap-16">

            {/* Left: eyebrow + headline (B) + subtitle + CTA */}
            <div className="max-w-[560px]">
              <p
                className="landing-fade mb-6 flex items-center gap-2.5 text-ms-xs font-semibold uppercase tracking-widest text-brand"
                style={{ animationDelay: "0.1s" }}
              >
                <LogoMark className="h-6 w-6" />
                Volleyball highlights
              </p>

              {/* B: Word-by-word reveal */}
              <h1 className="text-ms-3xl font-semibold tracking-tight text-foreground sm:text-ms-4xl">
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
                className="landing-fade mt-6 max-w-[46ch] text-ms-lg text-muted"
                style={{ animationDelay: "0.75s" }}
              >
                Upload a full game and get back a filterable feed of its best rallies —
                the spikes, serves, digs, sets, and blocks worth keeping, each in its own clip.
              </p>

              <div className="landing-fade mt-10" style={{ animationDelay: "0.9s" }}>
                <LandingCtas />
              </div>
            </div>

            {/* Right: C — mock clip card stack. A 4:3 box that fills its column
                (capped so the cards stay card-sized on a wide screen); the
                cards are placed and sized in percentages of it, and their
                text is in em, so the whole fan scales together. `text-ms-lg`
                is the cards' base size here, a step up from the sample grid. */}
            <div
              className="landing-fade relative hidden aspect-[4/3] w-full max-w-[440px] justify-self-end text-ms-lg lg:block"
              style={{ animationDelay: "0.4s" }}
              aria-hidden
            >
              {HERO_CLIPS.map(({ pos, floatClass, ...clip }) => (
                <div key={clip.action} style={pos}>
                  <div className={floatClass}>
                    <MockClipCard {...clip} className="w-full" />
                  </div>
                </div>
              ))}
            </div>

          </div>
        </div>
      </div>

      {/* ── Demo video ──────────────────────────────────────────── */}
      <section className="py-16" aria-labelledby="landing-demo">
        <SectionHeading id="landing-demo" eyebrow="See it run">
          A full game in, a highlight reel out
        </SectionHeading>
        <DemoVideo />
      </section>

      <div className="h-px bg-border" />

      {/* ── How it works ────────────────────────────────────────── */}
      <section className="py-16" aria-labelledby="landing-how">
        <SectionHeading id="landing-how" eyebrow="How it works">
          Four steps, two of them automatic
        </SectionHeading>
        {/* Two columns, not four: at body size a four-up row gives under 30
            characters a line. Each step's copy is capped at a readable measure. */}
        <div className="grid grid-cols-1 gap-x-12 gap-y-10 sm:grid-cols-2 stagger">
          {STEPS.map(({ step, title, body }) => (
            <div key={step} className="max-w-[52ch]">
              <p className="mb-3 text-ms-xs font-semibold tabular-nums text-brand">
                {step}
              </p>
              <h3 className="text-ms-lg font-semibold text-foreground">{title}</h3>
              <p className="mt-2 text-ms-base text-muted">{body}</p>
            </div>
          ))}
        </div>
      </section>

      <div className="h-px bg-border" />

      {/* ── Sample output ───────────────────────────────────────── */}
      <section className="py-16" aria-labelledby="landing-sample">
        <SectionHeading id="landing-sample" eyebrow="What you get">
          Every play, cut and labelled
        </SectionHeading>
        <SampleOutput
          clips={SAMPLE_CLIPS}
          label="Example clips, for illustration — not from a real game."
        />
      </section>

      {/* ── Closing sign-up band ────────────────────────────────── */}
      <section
        className="mb-4 rounded-xl border border-border bg-surface px-6 py-12 sm:px-10"
        aria-labelledby="landing-cta"
      >
        <h2
          id="landing-cta"
          className="text-ms-xl font-semibold tracking-tight text-foreground sm:text-ms-2xl"
        >
          Turn your next game into highlights.
        </h2>
        <p className="mt-3 max-w-[46ch] text-ms-base text-muted">
          Create an account, upload a game, and come back to its clips.
        </p>
        <div className="mt-8">
          <LandingCtas />
        </div>
      </section>

    </div>
  );
}
