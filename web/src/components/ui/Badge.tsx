import { cn } from "@/lib/utils";
import { type ActionType } from "@/lib/api";

const ACTION_STYLES: Record<string, { bg: string; text: string; dot: string }> = {
  spike:        { bg: "bg-red-500/8",     text: "text-red-400",     dot: "bg-red-400" },
  serve:        { bg: "bg-sky-500/8",     text: "text-sky-400",     dot: "bg-sky-400" },
  dig:          { bg: "bg-emerald-500/8", text: "text-emerald-400", dot: "bg-emerald-400" },
  set:          { bg: "bg-violet-500/8",  text: "text-violet-400",  dot: "bg-violet-400" },
  block:        { bg: "bg-orange-500/8",  text: "text-orange-400",  dot: "bg-orange-400" },
  unknown:      { bg: "bg-zinc-500/8",    text: "text-zinc-500",    dot: "bg-zinc-500" },
  removed:      { bg: "bg-zinc-800/60",   text: "text-zinc-600",    dot: "bg-zinc-600" },
  not_an_action:{ bg: "bg-zinc-800/60",   text: "text-zinc-600",    dot: "bg-zinc-600" },
};

interface BadgeProps {
  label: string;
  action?: ActionType | string;
  className?: string;
}

export function Badge({ label, action, className }: BadgeProps) {
  const key = action ?? label;
  const style = ACTION_STYLES[key] ?? ACTION_STYLES.unknown;

  const display =
    label === "not_an_action" ? "removed"
    : label === "unknown"     ? "unknown"
    : label;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded px-1.5 py-0.5",
        "text-[10px] font-semibold uppercase tracking-widest",
        style.bg,
        style.text,
        className
      )}
    >
      <span className={cn("h-1 w-1 rounded-full shrink-0", style.dot)} />
      {display}
    </span>
  );
}
