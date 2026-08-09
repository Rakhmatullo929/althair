"use client";

import * as AccordionPrimitive from "@radix-ui/react-accordion";
import { ChevronDown } from "lucide-react";
import { cn } from "./lib";

export function Accordion({
  items,
  className,
}: {
  items: { id: string; question: string; answer: string }[];
  className?: string;
}) {
  return (
    <AccordionPrimitive.Root
      type="single"
      collapsible
      className={cn("divide-border divide-y", className)}
    >
      {items.map((item) => (
        <AccordionPrimitive.Item value={item.id} key={item.id}>
          <AccordionPrimitive.Header>
            <AccordionPrimitive.Trigger className="focus-ring text-ink group flex w-full items-center justify-between gap-6 rounded-md py-5 text-left text-base font-semibold sm:text-lg">
              {item.question}
              <ChevronDown className="text-muted size-5 shrink-0 transition-transform duration-200 group-data-[state=open]:rotate-180" />
            </AccordionPrimitive.Trigger>
          </AccordionPrimitive.Header>
          <AccordionPrimitive.Content className="text-secondary overflow-hidden text-sm leading-7 data-[state=closed]:animate-[accordion-up_.2s_ease-out] data-[state=open]:animate-[accordion-down_.2s_ease-out] motion-reduce:animate-none sm:text-base">
            <div className="pr-10 pb-5">{item.answer}</div>
          </AccordionPrimitive.Content>
        </AccordionPrimitive.Item>
      ))}
    </AccordionPrimitive.Root>
  );
}
