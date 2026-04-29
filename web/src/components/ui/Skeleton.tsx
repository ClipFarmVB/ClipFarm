import { cn } from "@/lib/utils";

interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className }: SkeletonProps) {
  return <div className={cn("skeleton", className)} />;
}

export function ClipCardSkeleton() {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="skeleton aspect-video w-full" />
      <div className="px-3 py-2.5 space-y-2">
        <div className="flex items-center justify-between">
          <div className="skeleton h-3 w-16 rounded" />
          <div className="skeleton h-3 w-10 rounded" />
        </div>
      </div>
    </div>
  );
}

export function GameRowSkeleton() {
  return (
    <div className="flex items-center gap-4 rounded-lg border border-border bg-surface px-4 py-3.5">
      <div className="skeleton h-4 w-48 rounded" />
      <div className="skeleton ml-auto h-3 w-20 rounded" />
      <div className="skeleton h-5 w-16 rounded" />
      <div className="skeleton h-3 w-14 rounded" />
    </div>
  );
}
