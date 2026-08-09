import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
} from "react";
import { cn } from "./lib";

type ButtonVariant = "primary" | "secondary" | "ghost" | "link";

export function buttonStyles({
  variant = "primary",
  className,
}: {
  variant?: ButtonVariant;
  className?: string;
} = {}) {
  return cn(
    "focus-ring inline-flex min-h-11 items-center justify-center gap-2 rounded-[11px] px-5 py-2.5 text-sm font-semibold transition duration-200 disabled:pointer-events-none disabled:opacity-50",
    variant === "primary" &&
      "bg-primary text-white shadow-[0_10px_28px_rgba(8,80,52,.16)] hover:-translate-y-0.5 hover:bg-primary-hover",
    variant === "secondary" &&
      "border-border bg-white/75 text-ink border shadow-[0_1px_0_rgba(255,255,255,.8)] hover:-translate-y-0.5 hover:border-emerald-700/20 hover:bg-white",
    variant === "ghost" &&
      "text-secondary hover:bg-emerald-950/[.045] hover:text-ink",
    variant === "link" && "text-primary min-h-0 rounded-md p-0 hover:underline",
    className,
  );
}

export function Button({
  variant,
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      type={type}
      className={buttonStyles({ variant, className })}
      {...props}
    />
  );
}

export function Badge({
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "bg-primary-soft text-primary inline-flex items-center gap-2 rounded-full border border-emerald-900/10 px-3 py-1 text-xs font-semibold",
        className,
      )}
      {...props}
    />
  );
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "border-border rounded-2xl border bg-white shadow-[0_18px_50px_rgba(20,35,28,.055)]",
        className,
      )}
      {...props}
    />
  );
}

export function Container({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("container-shell", className)} {...props} />;
}

export function Section({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <section className={cn("section-space reveal", className)} {...props} />
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  align = "left",
  className,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  align?: "left" | "center";
  className?: string;
}) {
  return (
    <div
      className={cn(
        "max-w-2xl",
        align === "center" && "mx-auto text-center",
        className,
      )}
    >
      {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
      <h2 className="font-display text-ink mt-3 text-3xl leading-[1.08] font-[560] tracking-[-0.05em] text-balance sm:text-4xl lg:text-5xl">
        {title}
      </h2>
      {description ? (
        <p className="text-secondary mt-5 text-base leading-7 text-pretty sm:text-lg">
          {description}
        </p>
      ) : null}
    </div>
  );
}

export function IconTile({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "border-border grid size-11 shrink-0 place-items-center rounded-full border bg-white shadow-sm",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Field({
  label,
  error,
  hint,
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  error?: string;
  hint?: string;
}) {
  const id = props.id ?? props.name;
  const descriptionId = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <label className="block" htmlFor={id}>
      <span className="text-ink mb-1.5 block text-sm font-medium">{label}</span>
      <input
        id={id}
        aria-invalid={Boolean(error)}
        aria-describedby={descriptionId}
        className={cn(
          "border-border text-ink focus:border-primary focus:ring-primary/15 min-h-11 w-full rounded-xl border bg-white px-3.5 py-2.5 text-sm transition outline-none focus:ring-4",
          error && "border-red-500 focus:border-red-500 focus:ring-red-100",
          className,
        )}
        {...props}
      />
      {error ? (
        <span
          id={`${id}-error`}
          className="mt-1 block text-xs text-red-600"
          role="alert"
        >
          {error}
        </span>
      ) : hint ? (
        <span id={`${id}-hint`} className="text-muted mt-1 block text-xs">
          {hint}
        </span>
      ) : null}
    </label>
  );
}
