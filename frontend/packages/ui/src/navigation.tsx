"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Menu, X } from "lucide-react";
import { useState, type ReactNode } from "react";

export function MobileNavigation({
  label,
  closeLabel,
  children,
}: {
  label: string;
  closeLabel: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Trigger
        className="focus-ring text-ink grid size-11 place-items-center rounded-xl border border-slate-200 bg-white lg:hidden"
        aria-label={label}
      >
        <Menu className="size-5" />
      </DialogPrimitive.Trigger>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-slate-950/30" />
        <DialogPrimitive.Content className="fixed inset-x-3 top-3 z-50 rounded-[22px] bg-white p-5 shadow-2xl outline-none">
          <DialogPrimitive.Title className="sr-only">
            {label}
          </DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            {label}
          </DialogPrimitive.Description>
          <DialogPrimitive.Close
            className="focus-ring absolute top-4 right-4 grid size-10 place-items-center rounded-xl bg-slate-100"
            aria-label={closeLabel}
          >
            <X className="size-5" />
          </DialogPrimitive.Close>
          <div
            onClick={(event) => {
              if ((event.target as HTMLElement).closest("a")) setOpen(false);
            }}
          >
            {children}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function LanguageSwitcher({
  label,
  locale,
  items,
}: {
  label: string;
  locale: string;
  items: { locale: string; label: string; href: string }[];
}) {
  return (
    <label className="relative">
      <span className="sr-only">{label}</span>
      <select
        value={locale}
        onChange={(event) => {
          const item = items.find(
            (candidate) => candidate.locale === event.target.value,
          );
          if (item)
            window.location.assign(`${item.href}${window.location.hash}`);
        }}
        className="focus-ring border-border text-ink h-10 cursor-pointer appearance-none rounded-xl border bg-white py-2 pr-8 pl-3 text-xs font-semibold uppercase"
        aria-label={label}
      >
        {items.map((item) => (
          <option value={item.locale} key={item.locale}>
            {item.label}
          </option>
        ))}
      </select>
      <Chevron />
    </label>
  );
}

function Chevron() {
  return (
    <svg
      viewBox="0 0 12 12"
      className="pointer-events-none absolute top-1/2 right-2.5 size-3 -translate-y-1/2"
      aria-hidden="true"
    >
      <path
        d="m2.5 4.5 3.5 3 3.5-3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      />
    </svg>
  );
}
