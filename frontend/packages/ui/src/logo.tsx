import { brand } from "@workspace/brand";
import { BrandMark } from "@workspace/brand/mark";
import { cn } from "./lib";

export { BrandMark as LogoMark };

export function Wordmark({ className }: { className?: string }) {
  return (
    <span
      className={cn("text-ink text-base font-bold tracking-tight", className)}
    >
      {brand.name}
    </span>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <BrandMark className="size-9" aria-hidden="true" />
      <Wordmark />
    </span>
  );
}
