"use client";

import { Badge, Card, cn } from "@workspace/ui";
import { Bot, CheckCircle2, ShieldCheck, UserRound } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRef, useState, type KeyboardEvent } from "react";

type Scenario = {
  id: string;
  label: string;
  messages: { from: "client" | "ai"; text: string }[];
  context: string[];
  permission: string;
  source: string;
  customer: string;
  action: string;
};

type ReceiptCopy = {
  source: string;
  customer: string;
  status: string;
  statusValue: string;
};

export function ScenarioDemo() {
  const t = useTranslations("scenario");
  const scenarios = t.raw("items") as Scenario[];
  const receipt = t.raw("receipt") as ReceiptCopy;
  const [activeId, setActiveId] = useState(scenarios[0]?.id ?? "");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const active = scenarios.find((item) => item.id === activeId) ?? scenarios[0];

  if (!active) return null;

  const activateTab = (index: number) => {
    const next = scenarios[index];
    if (!next) return;
    setActiveId(next.id);
    window.requestAnimationFrame(() => tabRefs.current[index]?.focus());
  };

  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }

    event.preventDefault();
    if (event.key === "Home") return activateTab(0);
    if (event.key === "End") return activateTab(scenarios.length - 1);
    const direction = event.key === "ArrowRight" ? 1 : -1;
    activateTab((index + direction + scenarios.length) % scenarios.length);
  };

  return (
    <div>
      <div
        role="tablist"
        aria-label={t("demoLabel")}
        className="mb-5 flex gap-2 overflow-x-auto pb-2"
      >
        {scenarios.map((scenario, index) => (
          <button
            key={scenario.id}
            ref={(element) => {
              tabRefs.current[index] = element;
            }}
            id={`scenario-tab-${scenario.id}`}
            type="button"
            role="tab"
            aria-selected={active.id === scenario.id}
            aria-controls={`scenario-panel-${scenario.id}`}
            tabIndex={active.id === scenario.id ? 0 : -1}
            onClick={() => setActiveId(scenario.id)}
            onKeyDown={(event) => handleTabKeyDown(event, index)}
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
      {scenarios.map((scenario) => (
        <Card
          key={scenario.id}
          id={`scenario-panel-${scenario.id}`}
          role="tabpanel"
          aria-labelledby={`scenario-tab-${scenario.id}`}
          tabIndex={active.id === scenario.id ? 0 : -1}
          hidden={active.id !== scenario.id}
          className="scenario-card grid min-h-[430px] overflow-hidden lg:grid-cols-[minmax(0,1fr)_310px]"
        >
          <div className="bg-section p-5 sm:p-7">
            <Badge>
              <span className="size-1.5 rounded-full bg-emerald-500" />
              {t("demoLabel")}
            </Badge>
            <div className="mt-7 space-y-4">
              {scenario.messages.map((message, index) => (
                <div
                  key={`${scenario.id}-${index}`}
                  className={cn(
                    "scenario-message flex items-end gap-2",
                    message.from === "client" ? "justify-start" : "justify-end",
                  )}
                >
                  {message.from === "client" ? (
                    <span
                      className="grid size-8 shrink-0 place-items-center rounded-full bg-slate-200"
                      aria-hidden="true"
                    >
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
                    <span className="sr-only">
                      {t(`sender.${message.from}`)}:{" "}
                    </span>
                    {message.text}
                  </div>
                  {message.from === "ai" ? (
                    <span
                      className="bg-primary grid size-8 shrink-0 place-items-center rounded-full text-white"
                      aria-hidden="true"
                    >
                      <Bot className="size-4" />
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
            <div className="scenario-context">
              <p>{t("contextLabel")}</p>
              <div className="scenario-context-chips">
                {scenario.context.map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
              <div className="scenario-permission">
                <ShieldCheck aria-hidden="true" />
                <span>
                  <small>{t("permissionLabel")}</small>
                  <strong>{scenario.permission}</strong>
                </span>
              </div>
            </div>
          </div>
          <aside className="scenario-receipt">
            <div className="scenario-receipt-inner">
              <span className="scenario-receipt-icon" aria-hidden="true">
                <CheckCircle2 />
              </span>
              <p className="text-primary mt-4 text-xs font-bold tracking-wider uppercase">
                {t("result")}
              </p>
              <p className="text-ink mt-2 leading-6 font-semibold">
                {scenario.action}
              </p>
              <dl className="scenario-receipt-data">
                <div>
                  <dt>{receipt.source}</dt>
                  <dd>{scenario.source}</dd>
                </div>
                <div>
                  <dt>{receipt.customer}</dt>
                  <dd>{scenario.customer}</dd>
                </div>
                <div>
                  <dt>{receipt.status}</dt>
                  <dd>
                    <span aria-hidden="true" />
                    {receipt.statusValue}
                  </dd>
                </div>
              </dl>
            </div>
          </aside>
        </Card>
      ))}
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {t("result")}: {active.action}
      </p>
    </div>
  );
}
