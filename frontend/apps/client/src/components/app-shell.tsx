"use client";

import { Logo, MobileNavigation } from "@workspace/ui";
import {
  Bell,
  Bot,
  Building2,
  ChevronRight,
  GitBranch,
  LayoutDashboard,
  LogOut,
  Radio,
  Settings2,
  Users,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageSkeleton } from "./ui";

const navItems = [
  ["overview", "/app", LayoutDashboard],
  ["company", "/app/settings/company", Building2],
  ["branches", "/app/settings/branches", GitBranch],
  ["team", "/app/settings/team", Users],
  ["channels", "/app/settings/channels", Radio],
  ["aiContext", "/app/settings/ai-context", Bot],
] as const;

function Navigation() {
  const t = useTranslations("navigation");
  const pathname = usePathname();
  return (
    <nav aria-label={t("primary")} className="app-nav">
      {navItems.map(([key, href, Icon]) => {
        const active =
          href === "/app"
            ? /\/app\/?$/.test(pathname)
            : pathname.includes(href);
        return (
          <Link
            key={key}
            href={href}
            className={active ? "active" : undefined}
            aria-current={active ? "page" : undefined}
          >
            <Icon aria-hidden="true" />
            <span>{t(key)}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const t = useTranslations();
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const workspace = useWorkspace();

  if (workspace.loading || workspace.switching)
    return (
      <div className="shell-loading">
        <PageSkeleton />
      </div>
    );
  if (workspace.error && workspace.error.status !== 401) {
    return (
      <div className="shell-loading">
        <ErrorState
          title={t("errors.loadTitle")}
          description={workspace.error.message}
          requestId={workspace.error.requestId}
          onRetry={() => void workspace.refreshUser()}
        />
      </div>
    );
  }
  if (
    !workspace.user ||
    !workspace.membership ||
    !workspace.selectedOrganizationId
  )
    return (
      <div className="shell-loading">
        <PageSkeleton />
      </div>
    );

  const segments = pathname.split("/").filter(Boolean).slice(2);
  const roleLabel = t(`roles.${workspace.membership.role}`);
  const changeLocale = (nextLocale: string) => {
    const rest = pathname.split("/").slice(2).join("/");
    router.push(`/${nextLocale}/${rest}`);
  };

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <Link
          href="/app"
          className="sidebar-logo"
          aria-label={t("common.home")}
        >
          <Logo />
        </Link>
        <Navigation />
        <div className="sidebar-note">
          <Settings2 aria-hidden="true" />
          <div>
            <strong>{t("navigation.setupStage")}</strong>
            <span>{t("navigation.setupStageHint")}</span>
          </div>
        </div>
      </aside>
      <div className="app-column">
        <header className="topbar">
          <div className="mobile-menu">
            <MobileNavigation
              label={t("navigation.openMenu")}
              closeLabel={t("navigation.closeMenu")}
            >
              <div className="mobile-nav-logo">
                <Logo />
              </div>
              <Navigation />
            </MobileNavigation>
          </div>
          <label className="organization-switcher">
            <span>{t("organization.switchLabel")}</span>
            <select
              value={workspace.selectedOrganizationId}
              onChange={(event) =>
                workspace.selectOrganization(event.target.value)
              }
            >
              {workspace.user.memberships.map((membership) => (
                <option
                  key={membership.organization}
                  value={membership.organization}
                >
                  {membership.organization_name} ·{" "}
                  {t(`roles.${membership.role}`)}
                </option>
              ))}
            </select>
          </label>
          <div className="topbar-actions">
            <label className="locale-select">
              <span className="sr-only">{t("common.language")}</span>
              <select
                value={locale}
                onChange={(event) => changeLocale(event.target.value)}
                aria-label={t("common.language")}
              >
                <option value="ru">RU</option>
                <option value="uz">UZ</option>
                <option value="en">EN</option>
              </select>
            </label>
            <button
              className="icon-button"
              type="button"
              aria-label={t("navigation.notificationsLater")}
              title={t("navigation.notificationsLater")}
              disabled
            >
              <Bell aria-hidden="true" />
            </button>
            <details className="user-menu">
              <summary>
                <span className="avatar">
                  {(workspace.user.first_name || workspace.user.email)
                    .slice(0, 1)
                    .toUpperCase()}
                </span>
                <span className="user-copy">
                  <strong>
                    {workspace.user.first_name || workspace.user.email}
                  </strong>
                  <small>{roleLabel}</small>
                </span>
              </summary>
              <div className="user-popover">
                <p>{workspace.user.email}</p>
                <button onClick={() => void workspace.logout()}>
                  <LogOut aria-hidden="true" />
                  {t("auth.logout")}
                </button>
              </div>
            </details>
          </div>
        </header>
        {workspace.membership.organization_status === "suspended" ? (
          <div className="suspended-banner" role="status">
            {t("organization.suspendedBanner")}
          </div>
        ) : null}
        <div className="content-wrap">
          <nav className="breadcrumbs" aria-label={t("navigation.breadcrumbs")}>
            <Link href="/app">{t("navigation.overview")}</Link>
            {segments.slice(1).map((segment) => (
              <span key={segment}>
                <ChevronRight aria-hidden="true" />
                {segment.replaceAll("-", " ")}
              </span>
            ))}
          </nav>
          <main id="main-content">{children}</main>
        </div>
      </div>
    </div>
  );
}
