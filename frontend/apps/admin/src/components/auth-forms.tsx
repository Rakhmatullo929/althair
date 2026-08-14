"use client";

import { KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";
import { internalApi, InternalApiError } from "@/lib/api";

export function LoginForm() {
  const t = useTranslations("auth");
  const locale = useLocale();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await internalApi.login(email, password);
      router.replace(`/${locale}/mfa`);
    } catch (caught) {
      setError(
        caught instanceof InternalApiError ? caught.message : t("genericError"),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame
      icon={<LockKeyhole aria-hidden="true" />}
      title={t("title")}
      description={t("description")}
    >
      <form onSubmit={submit} className="auth-form">
        <label>
          <span>{t("email")}</span>
          <input
            autoComplete="username"
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label>
          <span>{t("password")}</span>
          <input
            autoComplete="current-password"
            type="password"
            required
            minLength={10}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <button className="primary-button" disabled={busy}>
          {busy ? "…" : t("submit")}
        </button>
      </form>
    </AuthFrame>
  );
}

export function MFAForm() {
  const t = useTranslations("auth");
  const locale = useLocale();
  const router = useRouter();
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState<Record<string, unknown> | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await internalApi.verifyMfa(code);
      router.replace(`/${locale}/app`);
    } catch (caught) {
      setError(
        caught instanceof InternalApiError ? caught.message : t("genericError"),
      );
    } finally {
      setBusy(false);
    }
  }

  async function setupMfa() {
    setBusy(true);
    setError("");
    try {
      setSetup(await internalApi.setupMfa());
    } catch (caught) {
      setError(
        caught instanceof InternalApiError ? caught.message : t("genericError"),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame
      icon={<ShieldCheck aria-hidden="true" />}
      title={t("mfaTitle")}
      description={t("mfaDescription")}
    >
      {setup ? (
        <section className="setup-secret" aria-label={t("setup")}>
          <strong>{t("setupHint")}</strong>
          <code>{String(setup.secret)}</code>
          <div className="recovery-grid">
            {(setup.recovery_codes as string[]).map((item) => (
              <code key={item}>{item}</code>
            ))}
          </div>
        </section>
      ) : null}
      <form onSubmit={submit} className="auth-form">
        <label>
          <span>{t("code")}</span>
          <input
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            minLength={6}
            maxLength={32}
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        </label>
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <button className="primary-button" disabled={busy}>
          {busy ? "…" : t("verify")}
        </button>
        {!setup ? (
          <button
            className="secondary-button"
            type="button"
            onClick={() => void setupMfa()}
            disabled={busy}
          >
            <KeyRound aria-hidden="true" />
            {t("setup")}
          </button>
        ) : null}
      </form>
    </AuthFrame>
  );
}

function AuthFrame({
  icon,
  title,
  description,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <main id="main-content" className="auth-page">
      <section className="auth-card">
        <div className="auth-mark">{icon}</div>
        <p className="eyebrow">Althair · Internal</p>
        <h1>{title}</h1>
        <p className="muted">{description}</p>
        {children}
        <footer>
          <ShieldCheck aria-hidden="true" /> Separate internal session · MFA
          protected
        </footer>
      </section>
    </main>
  );
}
