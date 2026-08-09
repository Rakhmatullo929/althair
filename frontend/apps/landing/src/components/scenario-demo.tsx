"use client";

import { Badge, Card, cn } from "@workspace/ui";
import { Bot, CheckCircle2, UserRound } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

type Scenario = {
  id: string;
  label: string;
  messages: { from: "client" | "ai"; text: string }[];
  action: string;
};

export function ScenarioDemo() {
  const t = useTranslations("scenario");
  const scenarios = t.raw("items") as Scenario[];
  const [activeId, setActiveId] = useState(scenarios[0]?.id ?? "");
  const active = scenarios.find((item) => item.id === activeId) ?? scenarios[0];

  if (!active) return null;

  return (
    <div>
      <div
        role="tablist"
        aria-label={t("demoLabel")}
        className="mb-5 flex gap-2 overflow-x-auto pb-2"
      >
        {scenarios.map((scenario) => (
          <button
            key={scenario.id}
            type="button"
            role="tab"
            aria-selected={active.id === scenario.id}
            onClick={() => setActiveId(scenario.id)}
            className={cn(
              "focus-ring shrink-0 rounded-xl border px-4 py-2.5 text-sm font-semibold transition",
              active.id === scenario.id
                ? "border-primary bg-primary text-white"
                : "border-border text-secondary bg-white hover:border-emerald-200",
            )}
          >
            {scenario.label}
          </button>
        ))}
      </div>
      <Card
        key={active.id}
        className="grid min-h-[360px] overflow-hidden lg:grid-cols-[1fr_280px]"
      >
        <div className="bg-section p-5 sm:p-7">
          <Badge>
            <span className="size-1.5 rounded-full bg-emerald-500" />
            {t("demoLabel")}
          </Badge>
          <div className="mt-7 space-y-4" aria-live="polite">
            {active.messages.map((message, index) => (
              <div
                key={`${active.id}-${index}`}
                className={cn(
                  "scenario-message flex items-end gap-2",
                  message.from === "client" ? "justify-start" : "justify-end",
                )}
              >
                {message.from === "client" ? (
                  <span className="grid size-8 shrink-0 place-items-center rounded-full bg-slate-200">
                    <UserRound className="size-4" />
                  </span>
                ) : null}
                <div
                  className={cn(
                    "max-w-[82%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm",
                    message.from === "client"
                      ? "rounded-bl-md bg-white text-slate-700"
                      : "bg-primary rounded-br-md text-white",
                  )}
                >
                  {message.text}
                </div>
                {message.from === "ai" ? (
                  <span className="bg-primary grid size-8 shrink-0 place-items-center rounded-full text-white">
                    <Bot className="size-4" />
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        </div>
        <div className="flex items-center border-t border-emerald-100 bg-emerald-50/70 p-6 lg:border-t-0 lg:border-l">
          <div>
            <CheckCircle2 className="text-primary size-9" />
            <p className="text-primary mt-4 text-xs font-bold tracking-wider uppercase">
              {t("result")}
            </p>
            <p className="text-ink mt-2 leading-6 font-semibold">
              {active.action}
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}
