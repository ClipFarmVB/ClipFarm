import { cn } from "@/lib/utils";

interface VolleyballProps {
  size?: number;
  className?: string;
}

export function Volleyball({ size = 24, className }: VolleyballProps) {
  return (
    <span
      className={cn("inline-block animate-spin", className)}
      style={{ fontSize: size, lineHeight: 1, width: size, height: size }}
      role="img"
      aria-label="Loading"
    >
      🏐
    </span>
  );
}
