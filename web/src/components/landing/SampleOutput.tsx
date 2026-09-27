import { MockClipCard, type MockClip } from "@/components/landing/MockClipCard";

/**
 * The "what you get" grid on the landing page.
 *
 * Prop-driven so the illustrative cards the page passes today can be swapped
 * for real clips from the sample game (CF-220) without changing this
 * component's interface. `label` says what the visitor is looking at — while
 * the cards are mock-ups it must say they are examples.
 */
export function SampleOutput({ clips, label }: { clips: MockClip[]; label: string }) {
  return (
    <div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
        {clips.map((clip) => (
          <MockClipCard key={`${clip.action}-${clip.time}`} {...clip} className="w-full" />
        ))}
      </div>
      <p className="mt-3 text-[11px] text-subtle">{label}</p>
    </div>
  );
}
