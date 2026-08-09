"use client";

import { brand } from "@workspace/brand";
import { LanguageSwitcher, Logo } from "@workspace/ui";
import { useLocale, useTranslations } from "next-intl";
import { usePathname, Link } from "@/i18n/navigation";

const locales = ["ru", "uz", "en"] as const;

export function SiteFooter() {
  const t = useTranslations();
  const locale = useLocale();
  const pathname = usePathname();
  const languageItems = locales.map((item) => ({
    locale: item,
    label: item,
    href: `/${item}${pathname === "/" ? "" : pathname}`,
  }));
  return (
    <footer className="border-t border-slate-100 bg-white py-12">
      <div className="container-shell">
        <div className="grid gap-10 md:grid-cols-[1.3fr_.7fr_.7fr]">
          <div>
            <Logo />
            <p className="text-secondary mt-4 max-w-sm text-sm leading-6">
              {t("footer.description")}
            </p>
            <span className="bg-primary-soft text-primary mt-4 inline-flex rounded-full px-2.5 py-1 text-[10px] font-bold uppercase">
              {t("footer.stage")}
            </span>
          </div>
          <div>
            <p className="text-ink text-sm font-bold">{t("footer.product")}</p>
            <nav className="text-secondary mt-4 grid gap-3 text-sm">
              <a href="#product">{t("nav.product")}</a>
              <a href="#channels">{t("nav.channels")}</a>
              <a href="#how">{t("nav.how")}</a>
              <a href="#faq">{t("nav.faq")}</a>
            </nav>
          </div>
          <div>
            <p className="text-ink text-sm font-bold">{t("footer.company")}</p>
            <div className="text-secondary mt-4 grid gap-3 text-sm">
              <Link href="/privacy">{t("footer.privacy")}</Link>
              <Link href="/terms">{t("footer.terms")}</Link>
              <a href={`mailto:${brand.primaryContactEmail}`}>
                {brand.primaryContactEmail}
              </a>
              {brand.telegramUrl ? (
                <a href={brand.telegramUrl} rel="noreferrer" target="_blank">
                  Telegram
                </a>
              ) : null}
            </div>
          </div>
        </div>
        <div className="mt-10 flex flex-col gap-4 border-t border-slate-100 pt-6 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-muted text-xs">
            © {new Date().getFullYear()} {brand.name}. {t("footer.rights")}
          </p>
          <LanguageSwitcher
            label={t("nav.language")}
            locale={locale}
            items={languageItems}
          />
        </div>
      </div>
    </footer>
  );
}
