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
    <footer className="border-t border-white/10 bg-[#071a13] py-14 text-white">
      <div className="container-shell">
        <div className="grid gap-10 md:grid-cols-[1.3fr_.7fr_.7fr]">
          <div>
            <Logo className="[&_svg]:brightness-0 [&_svg]:invert [&>span]:text-white" />
            <p className="mt-4 max-w-sm text-sm leading-6 text-emerald-50/55">
              {t("footer.description")}
            </p>
            <span className="mt-4 inline-flex rounded-full border border-[#dff79e]/15 bg-[#dff79e]/[.07] px-2.5 py-1 text-[10px] font-bold text-[#dff79e] uppercase">
              {t("footer.stage")}
            </span>
          </div>
          <div>
            <p className="text-sm font-bold text-white">
              {t("footer.product")}
            </p>
            <nav className="mt-4 grid gap-3 text-sm text-emerald-50/55">
              <a href="#product">{t("nav.product")}</a>
              <a href="#channels">{t("nav.channels")}</a>
              <a href="#how">{t("nav.how")}</a>
              <a href="#faq">{t("nav.faq")}</a>
            </nav>
          </div>
          <div>
            <p className="text-sm font-bold text-white">
              {t("footer.company")}
            </p>
            <div className="mt-4 grid gap-3 text-sm text-emerald-50/55">
              <Link href="/privacy">{t("footer.privacy")}</Link>
              <Link href="/terms">{t("footer.terms")}</Link>
              {brand.primaryContactEmail ? (
                <a href={`mailto:${brand.primaryContactEmail}`}>
                  {brand.primaryContactEmail}
                </a>
              ) : null}
              {brand.telegramUrl ? (
                <a href={brand.telegramUrl} rel="noreferrer" target="_blank">
                  Telegram
                </a>
              ) : null}
            </div>
          </div>
        </div>
        <div className="mt-10 flex flex-col gap-4 border-t border-white/10 pt-6 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-emerald-50/65">
            © {new Date().getFullYear()} {brand.name}. {t("footer.rights")}
          </p>
          <div className="[&_select]:border-white/10 [&_select]:bg-white/5 [&_select]:text-white">
            <LanguageSwitcher
              label={t("nav.language")}
              locale={locale}
              items={languageItems}
            />
          </div>
        </div>
      </div>
    </footer>
  );
}
