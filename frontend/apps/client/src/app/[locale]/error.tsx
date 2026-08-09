"use client";

import { useTranslations } from "next-intl";
import { useEffect } from "react";

export default function LocaleError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("errors");
  useEffect(() => {
    void error.digest;
  }, [error]);
  return (
    <main id="main-content" className="shell-loading">
      <section className="error-state" role="alert">
        <div>
          <h1>{t("unexpectedTitle")}</h1>
          <p>{t("unexpectedDescription")}</p>
        </div>
        <button className="button primary" onClick={reset}>
          {t("retry")}
        </button>
      </section>
    </main>
  );
}
