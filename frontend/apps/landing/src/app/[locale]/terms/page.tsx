import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { LegalPage } from "@/components/legal-page";

// LEGAL REVIEW REQUIRED: Replace this draft with approved counsel-reviewed text before public launch.
export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "metadata" });
  return { title: t("termsTitle"), robots: { index: false, follow: true } };
}

export default function TermsPage() {
  return <LegalPage kind="terms" />;
}
