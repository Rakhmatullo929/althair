import { Logo } from "@workspace/ui";
import { Link } from "@/i18n/navigation";
import { getTranslations } from "next-intl/server";

export default async function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const t = await getTranslations("auth");
  return (
    <main id="main-content" className="auth-page">
      <section className="auth-brand-panel" aria-label={t("panelLabel")}>
        <Link href="/login" className="auth-logo">
          <Logo />
        </Link>
        <div className="auth-brand-copy">
          <span className="eyebrow">{t("panelEyebrow")}</span>
          <h1>{t("panelTitle")}</h1>
          <p>{t("panelDescription")}</p>
        </div>
        <div className="auth-trust-row">
          <span>{t("panelSession")}</span>
          <span>{t("panelIsolation")}</span>
          <span>RU · UZ · EN</span>
        </div>
      </section>
      <section className="auth-form-panel">{children}</section>
    </main>
  );
}
