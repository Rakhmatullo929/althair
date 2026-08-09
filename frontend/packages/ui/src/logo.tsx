import { brand } from "@workspace/brand";
import { BrandMark } from "@workspace/brand/mark";
import { cn } from "./lib";

export { BrandMark as LogoMark };

export function Wordmark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "font-display text-ink text-base font-semibold tracking-[-0.035em]",
        className,
      )}
    >
      {brand.name}
    </span>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <BrandMark className="h-9 w-8" aria-hidden="true" />
      <Wordmark />
    </span>
  );
}
