"use client";

import {
  LanguageSwitcher,
  Logo,
  MobileNavigation,
  buttonStyles,
  cn,
} from "@workspace/ui";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { usePathname } from "@/i18n/navigation";
import { EarlyAccessDialog } from "./early-access-dialog";

const anchors = ["product", "channels", "how", "industries", "faq"] as const;
const locales = ["ru", "uz", "en"] as const;

export function SiteHeader() {
  const t = useTranslations("nav");
  const locale = useLocale();
  const pathname = usePathname();
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const languageItems = locales.map((item) => ({
    locale: item,
    label: item,
    href: `/${item}${pathname === "/" ? "" : pathname}`,
  }));

  return (
    <header
      className={cn(
        "fixed inset-x-0 top-0 z-40 border-b border-transparent transition duration-300",
        scrolled &&
          "border-emerald-950/10 bg-[#fbfcf8]/88 shadow-[0_10px_35px_rgba(20,35,28,.045)] backdrop-blur-xl",
      )}
    >
      <div className="container-shell flex h-[76px] items-center justify-between gap-4">
        <a href="#top" className="focus-ring rounded-xl" aria-label={t("home")}>
          <Logo />
        </a>
        <nav
          aria-label={t("primaryNav")}
          className="hidden items-center gap-1 lg:flex"
        >
          {anchors.map((anchor) => (
            <a
              href={`#${anchor}`}
              key={anchor}
              className={buttonStyles({
                variant: "ghost",
                className: "min-h-10 px-3 text-xs font-medium xl:text-sm",
              })}
            >
              {t(anchor)}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <LanguageSwitcher
            label={t("language")}
            locale={locale}
            items={languageItems}
          />
          <div className="hidden lg:block">
            <EarlyAccessDialog
              label={t("earlyAccess")}
              className="min-h-10 px-4 text-xs xl:text-sm"
            />
          </div>
          <MobileNavigation label={t("openMenu")} closeLabel={t("closeMenu")}>
            <Logo />
            <nav aria-label={t("mobileNav")} className="mt-8 grid gap-1">
              {anchors.map((anchor) => (
                <a
                  href={`#${anchor}`}
                  key={anchor}
                  className="focus-ring text-ink rounded-xl px-3 py-3 text-base font-semibold hover:bg-emerald-950/[.045]"
                >
                  {t(anchor)}
                </a>
              ))}
              <a
                href="#early-access"
                className={buttonStyles({ className: "mt-4" })}
              >
                {t("earlyAccess")}
              </a>
            </nav>
          </MobileNavigation>
        </div>
      </div>
    </header>
  );
}
