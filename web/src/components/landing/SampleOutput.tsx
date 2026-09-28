import { MockClipCard, type MockClip } from "@/components/landing/MockClipCard";

/**
 * The "what you get" grid on the landing page.
 *
 * Prop-driven so the page chooses the cards and the caption. `MockClip` holds
 * display fields and Tailwind classes but no thumbnail or video URL, so showing
 * real clips from the sample game (CF-220) will need that type to grow.
 * `label` says what the visitor is looking at — while the cards are mock-ups
 * it must say they are examples.
 */
export function SampleOutput({ clips, label }: { clips: MockClip[]; label: string }) {
  return (
    <div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
        {clips.map((clip) => (
          <MockClipCard key={`${clip.action}-${clip.time}`} {...clip} className="w-full" />
        ))}
      </div>
      <p className="mt-3 text-[11px] text-foreground">{label}</p>
    </div>
  );
}
