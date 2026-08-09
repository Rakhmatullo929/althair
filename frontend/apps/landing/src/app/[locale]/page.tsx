import { brand } from "@workspace/brand";
import { hasLocale } from "next-intl";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { notFound } from "next/navigation";
import { LandingSections } from "@/components/landing-sections";
import { routing } from "@/i18n/routing";

export default async function LandingPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "metadata" });
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: brand.name,
    applicationCategory: "BusinessApplication",
    operatingSystem: "Web",
    description: t("description"),
    url: `${brand.appUrl}/${locale}`,
    releaseNotes: "Pre-launch product concept",
  };
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c"),
        }}
      />
      <LandingSections />
    </>
  );
}
