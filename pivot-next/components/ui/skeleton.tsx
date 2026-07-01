import { cn } from "@/lib/utils";

function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "relative isolate overflow-hidden rounded-[calc(var(--radius-md)-2px)] border border-border/45",
        "bg-[linear-gradient(180deg,color-mix(in_srgb,var(--background)_78%,var(--muted))_0%,color-mix(in_srgb,var(--background)_62%,var(--muted))_100%)]",
        "shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]",
        "before:absolute before:inset-0 before:animate-[skeleton-shimmer_1.7s_ease-in-out_infinite]",
        "before:bg-[linear-gradient(110deg,transparent_0%,rgba(255,255,255,0.02)_28%,rgba(255,255,255,0.20)_48%,rgba(255,255,255,0.03)_68%,transparent_100%)]",
        "before:content-['']",
        className,
      )}
      aria-hidden="true"
      {...props}
    />
  );
}

export { Skeleton };