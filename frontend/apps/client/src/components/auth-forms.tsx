"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { ApiError } from "@workspace/api-client";
import { ArrowLeft, CheckCircle2, Copy, Eye, EyeOff } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Link } from "@/i18n/navigation";
import { publicApi } from "@/lib/public-api";
import { buildLoginSchema, buildRegistrationSchema } from "@/lib/validation";
import { SubmitButton } from "./ui";

function FormError({ error }: { error: ApiError | null }) {
  if (!error) return null;
  return (
    <div className="form-alert" role="alert">
      <strong>{error.message}</strong>
      {error.requestId ? <span>Request ID: {error.requestId}</span> : null}
    </div>
  );
}

function PasswordField({
  register,
  error,
  label,
  name = "password",
}: {
  register: ReturnType<typeof useForm>["register"];
  error?: string;
  label: string;
  name?: string;
}) {
  const [visible, setVisible] = useState(false);
  const inputId = useId();
  return (
    <div className="field">
      <label htmlFor={inputId}>{label}</label>
      <div className="password-input">
        <input
          id={inputId}
          type={visible ? "text" : "password"}
          autoComplete={
            name === "password" ? "current-password" : "new-password"
          }
          aria-invalid={Boolean(error)}
          {...register(name)}
        />
        <button
          type="button"
          onClick={() => setVisible((value) => !value)}
          aria-label={visible ? "Hide password" : "Show password"}
        >
          {visible ? <EyeOff /> : <Eye />}
        </button>
      </div>
      {error ? (
        <small className="field-error" role="alert">
          {error}
        </small>
      ) : null}
    </div>
  );
}

export function LoginForm() {
  const t = useTranslations("auth");
  const locale = useLocale();
  const router = useRouter();
  const [error, setError] = useState<ApiError | null>(null);
  const [forgot, setForgot] = useState(false);
  const [resetMessage, setResetMessage] = useState<string | null>(null);
  const schema = buildLoginSchema({
    emailInvalid: t("emailInvalid"),
    passwordRequired: t("passwordRequired"),
    passwordLength: t("passwordLength"),
    nameRequired: t("nameRequired"),
    companyRequired: t("companyRequired"),
  });
  type Values = z.infer<typeof schema>;
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });

  const submit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      if (forgot) {
        const result = await publicApi.requestPasswordReset(values.email);
        setResetMessage(result.detail);
        return;
      }
      await publicApi.login(values);
      router.replace(`/${locale}/app`);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError(t("genericError"), 500, "unknown"),
      );
    }
  });

  return (
    <div className="auth-card">
      <div className="auth-card-heading">
        {forgot ? (
          <button
            className="back-button"
            type="button"
            onClick={() => {
              setForgot(false);
              setResetMessage(null);
            }}
          >
            <ArrowLeft />
            {t("backToLogin")}
          </button>
        ) : null}
        <span className="eyebrow">
          {forgot ? t("resetEyebrow") : t("welcomeEyebrow")}
        </span>
        <h2>{forgot ? t("resetTitle") : t("loginTitle")}</h2>
        <p>{forgot ? t("resetDescription") : t("loginDescription")}</p>
      </div>
      {resetMessage ? (
        <div className="success-state" role="status">
          <CheckCircle2 />
          <p>{resetMessage}</p>
        </div>
      ) : (
        <form onSubmit={submit} noValidate>
          <FormError error={error} />
          <label className="field">
            <span>{t("email")}</span>
            <input
              type="email"
              autoComplete="email"
              aria-invalid={Boolean(form.formState.errors.email)}
              {...form.register("email")}
            />
            {form.formState.errors.email ? (
              <small className="field-error">
                {form.formState.errors.email.message}
              </small>
            ) : null}
          </label>
          {!forgot ? (
            <PasswordField
              register={form.register as never}
              error={form.formState.errors.password?.message}
              label={t("password")}
            />
          ) : null}
          <SubmitButton pending={form.formState.isSubmitting}>
            {forgot ? t("requestReset") : t("loginAction")}
          </SubmitButton>
          {!forgot ? (
            <button
              className="text-button"
              type="button"
              onClick={() => setForgot(true)}
            >
              {t("forgotPassword")}
            </button>
          ) : null}
        </form>
      )}
      {!forgot ? (
        <p className="auth-footer">
          {t("noAccount")} <Link href="/register">{t("createAccount")}</Link>
        </p>
      ) : null}
    </div>
  );
}

export function RegisterForm() {
  const t = useTranslations("auth");
  const locale = useLocale() as "ru" | "uz" | "en";
  const router = useRouter();
  const [error, setError] = useState<ApiError | null>(null);
  const schema = buildRegistrationSchema({
    emailInvalid: t("emailInvalid"),
    passwordRequired: t("passwordRequired"),
    passwordLength: t("passwordLength"),
    nameRequired: t("nameRequired"),
    companyRequired: t("companyRequired"),
  });
  type Values = z.infer<typeof schema>;
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      first_name: "",
      last_name: "",
      email: "",
      password: "",
      organization_name: "",
      industry: "generic",
    },
  });
  const submit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      await publicApi.register({
        ...values,
        default_language: locale,
        timezone: "Asia/Tashkent",
      });
      router.replace(`/${locale}/app/onboarding`);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError(t("genericError"), 500, "unknown"),
      );
    }
  });
  return (
    <div className="auth-card auth-card-wide">
      <div className="auth-card-heading">
        <span className="eyebrow">{t("registerEyebrow")}</span>
        <h2>{t("registerTitle")}</h2>
        <p>{t("registerDescription")}</p>
      </div>
      <form onSubmit={submit} noValidate>
        <FormError error={error} />
        <div className="form-grid two">
          <label className="field">
            <span>{t("firstName")}</span>
            <input autoComplete="given-name" {...form.register("first_name")} />
            {form.formState.errors.first_name ? (
              <small className="field-error">
                {form.formState.errors.first_name.message}
              </small>
            ) : null}
          </label>
          <label className="field">
            <span>{t("lastName")}</span>
            <input autoComplete="family-name" {...form.register("last_name")} />
          </label>
        </div>
        <label className="field">
          <span>{t("workEmail")}</span>
          <input
            type="email"
            autoComplete="email"
            {...form.register("email")}
          />
          {form.formState.errors.email ? (
            <small className="field-error">
              {form.formState.errors.email.message}
            </small>
          ) : null}
        </label>
        <PasswordField
          register={form.register as never}
          error={form.formState.errors.password?.message}
          label={t("createPassword")}
          name="password"
        />
        <label className="field">
          <span>{t("companyName")}</span>
          <input
            autoComplete="organization"
            {...form.register("organization_name")}
          />
          {form.formState.errors.organization_name ? (
            <small className="field-error">
              {form.formState.errors.organization_name.message}
            </small>
          ) : null}
        </label>
        <label className="field">
          <span>{t("industry")}</span>
          <select {...form.register("industry")}>
            <option value="generic">{t("industries.generic")}</option>
            <option value="beauty">{t("industries.beauty")}</option>
            <option value="clinic">{t("industries.clinic")}</option>
            <option value="education">{t("industries.education")}</option>
            <option value="auto_service">{t("industries.auto")}</option>
            <option value="other">{t("industries.other")}</option>
          </select>
        </label>
        <SubmitButton pending={form.formState.isSubmitting}>
          {t("registerAction")}
        </SubmitButton>
      </form>
      <p className="auth-footer">
        {t("hasAccount")} <Link href="/login">{t("signIn")}</Link>
      </p>
    </div>
  );
}

export function InvitationForm({ token }: { token: string }) {
  const t = useTranslations("invitation");
  const locale = useLocale();
  const router = useRouter();
  const [state, setState] = useState<Awaited<
    ReturnType<typeof publicApi.inspectInvitation>
  > | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [copied, setCopied] = useState(false);
  const schema = z.object({
    first_name: z.string().min(2),
    last_name: z.string(),
    password: z.string().min(10),
  });
  type Values = z.infer<typeof schema>;
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { first_name: "", last_name: "", password: "" },
  });
  useEffect(() => {
    void publicApi
      .inspectInvitation(token)
      .then(setState)
      .catch((caught) => setError(caught as ApiError));
  }, [token]);
  const submit = form.handleSubmit(async (values) => {
    try {
      const result = await publicApi.acceptInvitation({ token, ...values });
      window.localStorage.setItem(
        "aifo:selected-organization",
        result.organization_id,
      );
      router.replace(`/${locale}/app`);
    } catch (caught) {
      const nextError = caught as ApiError;
      if (nextError.code === "login_required")
        router.replace(`/${locale}/login`);
      else setError(nextError);
    }
  });
  if (!state && !error)
    return (
      <div className="auth-card">
        <div className="skeleton skeleton-panel" />
      </div>
    );
  if (error || state?.state === "invalid")
    return (
      <div className="auth-card state-card">
        <h2>{t("invalidTitle")}</h2>
        <p>{t("invalidDescription")}</p>
        <Link className="button primary" href="/login">
          {t("goToLogin")}
        </Link>
      </div>
    );
  if (state?.state !== "pending")
    return (
      <div className="auth-card state-card">
        <h2>{t(`${state?.state}Title`)}</h2>
        <p>{t(`${state?.state}Description`)}</p>
        <Link className="button primary" href="/login">
          {t("goToLogin")}
        </Link>
      </div>
    );
  return (
    <div className="auth-card">
      <div className="auth-card-heading">
        <span className="eyebrow">{t("eyebrow")}</span>
        <h2>{t("title", { company: state.organization_name ?? "" })}</h2>
        <p>
          {t("description", {
            email: state.email ?? "",
            role: state.role ?? "",
          })}
        </p>
      </div>
      <form onSubmit={submit}>
        <FormError error={error} />
        <div className="form-grid two">
          <label className="field">
            <span>{t("firstName")}</span>
            <input {...form.register("first_name")} />
          </label>
          <label className="field">
            <span>{t("lastName")}</span>
            <input {...form.register("last_name")} />
          </label>
        </div>
        <PasswordField
          register={form.register as never}
          label={t("password")}
          name="password"
        />
        <SubmitButton pending={form.formState.isSubmitting}>
          {t("accept")}
        </SubmitButton>
      </form>
      <button
        className="text-button copy-token"
        type="button"
        onClick={() =>
          void navigator.clipboard
            .writeText(window.location.href)
            .then(() => setCopied(true))
        }
      >
        <Copy />
        {copied ? t("copied") : t("copyLink")}
      </button>
    </div>
  );
}

export function ResetPasswordForm({ token }: { token: string }) {
  const t = useTranslations("auth");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const schema = z.object({
    password: z.string().min(10, t("passwordLength")),
  });
  const form = useForm<{ password: string }>({
    resolver: zodResolver(schema),
    defaultValues: { password: "" },
  });
  const submit = form.handleSubmit(async ({ password }) => {
    try {
      await publicApi.confirmPasswordReset(token, password);
      setDone(true);
    } catch (caught) {
      setError(caught as ApiError);
    }
  });
  return (
    <div className="auth-card">
      {done ? (
        <div className="success-state">
          <CheckCircle2 />
          <h2>{t("resetComplete")}</h2>
          <Link className="button primary" href="/login">
            {t("signIn")}
          </Link>
        </div>
      ) : (
        <>
          <div className="auth-card-heading">
            <h2>{t("choosePassword")}</h2>
            <p>{t("choosePasswordDescription")}</p>
          </div>
          <form onSubmit={submit}>
            <FormError error={error} />
            <PasswordField
              register={form.register as never}
              label={t("createPassword")}
              name="password"
            />
            <SubmitButton pending={form.formState.isSubmitting}>
              {t("savePassword")}
            </SubmitButton>
          </form>
        </>
      )}
    </div>
  );
}
