import { brand } from "@workspace/brand";
import type { Metadata } from "next";
import { hasLocale, NextIntlClientProvider } from "next-intl";
import {
  getMessages,
  getTranslations,
  setRequestLocale,
} from "next-intl/server";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { routing } from "@/i18n/routing";

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  const t = await getTranslations({ locale, namespace: "metadata" });
  const path = `/${locale}`;
  return {
    metadataBase: new URL(brand.appUrl),
    title: {
      default: `${t("title")} | ${brand.name}`,
      template: `%s | ${brand.name}`,
    },
    description: t("description"),
    alternates: {
      canonical: path,
      languages: { ru: "/ru", uz: "/uz", en: "/en", "x-default": "/ru" },
    },
    openGraph: {
      type: "website",
      locale,
      url: path,
      siteName: brand.name,
      title: `${t("title")} | ${brand.name}`,
      description: t("description"),
    },
    twitter: {
      card: "summary_large_image",
      title: `${t("title")} | ${brand.name}`,
      description: t("description"),
    },
  };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const messages = await getMessages();
  const nav = await getTranslations({ locale, namespace: "nav" });
  return (
    <NextIntlClientProvider messages={messages}>
      <a
        href="#main"
        className="focus:bg-primary sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[100] focus:rounded-lg focus:px-4 focus:py-2 focus:text-white"
      >
        {nav("skip")}
      </a>
      <SiteHeader />
      <main id="main">{children}</main>
      <SiteFooter />
    </NextIntlClientProvider>
  );
}
