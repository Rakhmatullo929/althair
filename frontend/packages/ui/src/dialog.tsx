"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "./lib";

export function Dialog({
  trigger,
  title,
  description,
  closeLabel,
  children,
  open,
  onOpenChange,
  className,
}: {
  trigger?: ReactNode;
  title: string;
  description?: string;
  closeLabel: string;
  children: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      {trigger ? (
        <DialogPrimitive.Trigger asChild>{trigger}</DialogPrimitive.Trigger>
      ) : null}
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-slate-950/35 backdrop-blur-sm data-[state=closed]:animate-[fade-out_.18s_ease] data-[state=open]:animate-[fade-in_.18s_ease] motion-reduce:animate-none" />
        <DialogPrimitive.Content
          className={cn(
            "border-border fixed top-1/2 left-1/2 z-50 max-h-[90dvh] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[22px] border bg-white p-5 shadow-2xl outline-none sm:p-7",
            className,
          )}
        >
          <DialogPrimitive.Title className="text-ink pr-10 text-2xl font-bold tracking-tight">
            {title}
          </DialogPrimitive.Title>
          {description ? (
            <DialogPrimitive.Description className="text-secondary mt-2 text-sm leading-6">
              {description}
            </DialogPrimitive.Description>
          ) : null}
          <DialogPrimitive.Close
            className="focus-ring text-secondary hover:text-ink absolute top-5 right-5 grid size-9 place-items-center rounded-lg hover:bg-slate-100"
            aria-label={closeLabel}
          >
            <X className="size-5" />
          </DialogPrimitive.Close>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
