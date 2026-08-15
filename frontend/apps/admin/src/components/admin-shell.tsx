"use client";

import {
  Activity,
  Bot,
  Building2,
  CreditCard,
  DatabaseZap,
  FileClock,
  Gauge,
  HeartPulse,
  KeyRound,
  LogOut,
  Menu,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { internalApi, InternalApiError, type InternalMe } from "@/lib/api";
import { Link } from "@/i18n/navigation";

const nav = [
  ["overview", "/app", Gauge],
  ["organizations", "/app/organizations", Building2],
  ["providers", "/app/providers", HeartPulse],
  ["ai", "/app/ai", Bot],
  ["jobs", "/app/jobs", DatabaseZap],
  ["incidents", "/app/incidents", ShieldAlert],
  ["dataRequests", "/app/data-requests", FileClock],
  ["entitlements", "/app/entitlements", KeyRound],
  ["billing", "/app/billing/plans", CreditCard],
  ["audit", "/app/audit", Activity],
  ["staff", "/app/platform-staff", Users],
  ["settings", "/app/settings", Settings],
] as const;

const SessionContext = createContext<InternalMe | null>(null);
export const useInternalSession = () => useContext(SessionContext);

export function AdminShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations();
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<InternalMe | null>(null);
  const [error, setError] = useState("");
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    let active = true;
    void internalApi
      .me()
      .then((value) => active && setMe(value))
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof InternalApiError && caught.status === 401)
          router.replace(`/${locale}/login`);
        else
          setError(
            caught instanceof Error
              ? caught.message
              : "Internal session unavailable",
          );
      });
    return () => {
      active = false;
    };
  }, [locale, router]);

  useEffect(() => {
    if (!me) return;
    const expires = new Date(me.session_expires_at).getTime();
    const delay = Math.max(0, expires - Date.now());
    const timer = window.setTimeout(
      () => router.replace(`/${locale}/login?expired=1`),
      delay,
    );
    return () => window.clearTimeout(timer);
  }, [locale, me, router]);

  const activeTitle = useMemo(
    () =>
      nav.find(([, href]) =>
        href === "/app" ? /\/app\/?$/.test(pathname) : pathname.includes(href),
      )?.[0] ?? "overview",
    [pathname],
  );
  if (error)
    return (
      <main className="center-state">
        <ShieldAlert />
        <h1>{error}</h1>
        <button onClick={() => window.location.reload()}>
          {t("common.retry")}
        </button>
      </main>
    );
  if (!me)
    return (
      <main className="center-state" aria-live="polite">
        <span className="spinner" />
        {t("common.loading")}
      </main>
    );

  return (
    <SessionContext.Provider value={me}>
      <div className="admin-frame">
        <aside className={menu ? "admin-sidebar open" : "admin-sidebar"}>
          <div className="admin-brand">
            <span>AI</span>
            <div>
              <strong>{t("common.product")}</strong>
              <small>{t("common.internal")}</small>
            </div>
          </div>
          <button
            className="mobile-close"
            onClick={() => setMenu(false)}
            aria-label="Close menu"
          >
            <X />
          </button>
          <nav aria-label="Internal operations">
            {nav.map(([key, href, Icon]) => {
              const active =
                href === "/app"
                  ? /\/app\/?$/.test(pathname)
                  : pathname.includes(href);
              return (
                <Link
                  key={key}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={active ? "active" : ""}
                  onClick={() => setMenu(false)}
                >
                  <Icon aria-hidden="true" />
                  <span>{t(`nav.${key}`)}</span>
                </Link>
              );
            })}
          </nav>
          <div className="security-note">
            <ShieldCheck />
            <div>
              <strong>No impersonation</strong>
              <span>Read-only inspection · audited actions</span>
            </div>
          </div>
        </aside>
        <div className="admin-column">
          <header className="admin-topbar">
            <button
              className="mobile-menu"
              onClick={() => setMenu(true)}
              aria-label="Open menu"
            >
              <Menu />
            </button>
            <div>
              <p>{t(`pages.${activeTitle}`)}</p>
              <span className="environment">
                <i />
                {t("common.environment")}: {me.environment}
              </span>
            </div>
            <div className="session-identity">
              <span className={me.mfa_fresh ? "mfa fresh" : "mfa"}>
                <ShieldCheck />
                MFA {me.mfa_fresh ? "fresh" : "verified"}
              </span>
              <div>
                <strong>{me.display_name}</strong>
                <small>{me.role.replaceAll("_", " ")}</small>
              </div>
              <select
                aria-label={t("common.language")}
                value={locale}
                onChange={(event) =>
                  router.push(
                    pathname.replace(`/${locale}/`, `/${event.target.value}/`),
                  )
                }
              >
                <option value="en">EN</option>
                <option value="ru">RU</option>
              </select>
              <button
                aria-label={t("auth.logout")}
                onClick={() =>
                  void internalApi
                    .logout()
                    .finally(() => router.replace(`/${locale}/login`))
                }
              >
                <LogOut />
              </button>
            </div>
          </header>
          <main id="main-content" className="admin-content">
            {children}
          </main>
        </div>
      </div>
    </SessionContext.Provider>
  );
}
