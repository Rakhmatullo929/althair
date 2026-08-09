"use client";

import { Button, Dialog, Field, buttonStyles, cn } from "@workspace/ui";
import { CheckCircle2, LoaderCircle, TriangleAlert } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRef, useState, type FormEvent } from "react";
import { Link } from "@/i18n/navigation";
import {
  earlyAccessSchema,
  type EarlyAccessResponse,
} from "@/lib/early-access";

type FieldErrors = Record<string, string | undefined>;

export function EarlyAccessDialog({
  label,
  variant = "primary",
  className,
}: {
  label: string;
  variant?: "primary" | "secondary";
  className?: string;
}) {
  const t = useTranslations("form");
  const locale = useLocale() as "ru" | "uz" | "en";
  const startedAt = useRef(0);
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [status, setStatus] = useState<"idle" | "success" | "demo" | "error">(
    "idle",
  );

  const industries = t.raw("industries") as string[];
  const channels = t.raw("channels") as string[];

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    setStatus("idle");
    const form = new FormData(formElement);
    const input = {
      fullName: form.get("fullName"),
      companyName: form.get("companyName"),
      contact: form.get("contact"),
      industry: form.get("industry"),
      preferredChannel: form.get("preferredChannel"),
      note: form.get("note"),
      consent: form.get("consent") === "on",
      website: form.get("website"),
      startedAt: startedAt.current,
      locale,
    };
    const parsed = earlyAccessSchema.safeParse(input);

    if (!parsed.success) {
      const fields = parsed.error.flatten().fieldErrors;
      setErrors({
        fullName: fields.fullName ? t("required") : undefined,
        companyName: fields.companyName ? t("required") : undefined,
        contact: fields.contact ? t("invalidContact") : undefined,
        industry: fields.industry ? t("required") : undefined,
        preferredChannel: fields.preferredChannel ? t("required") : undefined,
        consent: fields.consent ? t("consentRequired") : undefined,
      });
      return;
    }

    setErrors({});
    setSubmitting(true);
    try {
      const response = await fetch("/api/early-access", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(parsed.data),
      });
      const result = (await response.json()) as EarlyAccessResponse;
      if (result.ok) {
        setStatus("success");
        formElement.reset();
      } else {
        setStatus(result.code === "DEMO_MODE" ? "demo" : "error");
      }
    } catch {
      setStatus("error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (value) startedAt.current = Date.now();
      }}
      trigger={
        <Button
          variant={variant}
          className={className}
          data-testid="early-access-trigger"
        >
          {label}
        </Button>
      }
      title={t("title")}
      description={t("description")}
      closeLabel={t("close")}
    >
      <form className="mt-6 space-y-4" onSubmit={handleSubmit} noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            name="fullName"
            label={t("name")}
            autoComplete="name"
            error={errors.fullName}
          />
          <Field
            name="companyName"
            label={t("company")}
            autoComplete="organization"
            error={errors.companyName}
          />
        </div>
        <Field
          name="contact"
          label={t("contact")}
          autoComplete="email"
          error={errors.contact}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <SelectField
            name="industry"
            label={t("industry")}
            placeholder={t("select")}
            items={industries}
            error={errors.industry}
          />
          <SelectField
            name="preferredChannel"
            label={t("channel")}
            placeholder={t("select")}
            items={channels}
            error={errors.preferredChannel}
          />
        </div>
        <label className="block">
          <span className="text-ink mb-1.5 block text-sm font-medium">
            {t("note")}
          </span>
          <textarea
            name="note"
            rows={3}
            maxLength={1000}
            className="border-border text-ink focus:border-primary focus:ring-primary/15 w-full resize-y rounded-xl border px-3.5 py-2.5 text-sm outline-none focus:ring-4"
          />
        </label>
        <div
          className="pointer-events-none absolute -left-[9999px]"
          aria-hidden="true"
        >
          <label>
            Website
            <input name="website" tabIndex={-1} autoComplete="off" />
          </label>
        </div>
        <label className="flex items-start gap-3 text-sm">
          <input
            name="consent"
            type="checkbox"
            className="accent-primary mt-0.5 size-4 shrink-0"
            aria-invalid={Boolean(errors.consent)}
            aria-describedby={errors.consent ? "consent-error" : undefined}
          />
          <span className="text-secondary">
            {t("consentBefore")}{" "}
            <Link
              href="/privacy"
              className="text-primary underline underline-offset-2"
              target="_blank"
            >
              {t("consentLink")}
            </Link>
            .
            {errors.consent ? (
              <span
                id="consent-error"
                className="block text-xs text-red-600"
                role="alert"
              >
                {errors.consent}
              </span>
            ) : null}
          </span>
        </label>
        {status !== "idle" ? (
          <div
            className={cn(
              "flex gap-3 rounded-xl border p-3 text-sm leading-6",
              status === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : status === "demo"
                  ? "border-amber-200 bg-amber-50 text-amber-900"
                  : "border-red-200 bg-red-50 text-red-700",
            )}
            role="status"
            aria-live="polite"
          >
            {status === "success" ? (
              <CheckCircle2 className="mt-0.5 size-5 shrink-0" />
            ) : (
              <TriangleAlert className="mt-0.5 size-5 shrink-0" />
            )}
            <span>{t(status)}</span>
          </div>
        ) : null}
        <button
          type="submit"
          disabled={submitting}
          className={buttonStyles({ className: "w-full" })}
        >
          {submitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
          {submitting ? t("sending") : t("submit")}
        </button>
      </form>
    </Dialog>
  );
}

function SelectField({
  name,
  label,
  placeholder,
  items,
  error,
}: {
  name: string;
  label: string;
  placeholder: string;
  items: string[];
  error?: string;
}) {
  const errorId = `${name}-error`;
  return (
    <label className="block">
      <span className="text-ink mb-1.5 block text-sm font-medium">{label}</span>
      <select
        name={name}
        defaultValue=""
        aria-invalid={Boolean(error)}
        aria-describedby={error ? errorId : undefined}
        className={cn(
          "border-border text-ink focus:border-primary focus:ring-primary/15 min-h-11 w-full rounded-xl border bg-white px-3.5 py-2.5 text-sm outline-none focus:ring-4",
          error && "border-red-500",
        )}
      >
        <option value="" disabled>
          {placeholder}
        </option>
        {items.map((item) => (
          <option value={item} key={item}>
            {item}
          </option>
        ))}
      </select>
      {error ? (
        <span
          id={errorId}
          className="mt-1 block text-xs text-red-600"
          role="alert"
        >
          {error}
        </span>
      ) : null}
    </label>
  );
}
