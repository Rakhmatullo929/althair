import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";

export default async function NotFound() {
  const t = await getTranslations("errors");
  return (
    <main id="main-content" className="shell-loading">
      <section className="empty-state">
        <h1>{t("notFoundTitle")}</h1>
        <p>{t("notFoundDescription")}</p>
        <Link className="button primary" href="/app">
          {t("backHome")}
        </Link>
      </section>
    </main>
  );
}
