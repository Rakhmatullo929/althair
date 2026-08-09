import { brand } from "@workspace/brand";
import { Badge, Card, Container, buttonStyles } from "@workspace/ui";
import { ArrowLeft, Scale } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { Link } from "@/i18n/navigation";

type LegalSection = { title: string; text: string };

export async function LegalPage({ kind }: { kind: "privacy" | "terms" }) {
  const t = await getTranslations("legal");
  const sections = t.raw(`${kind}.sections`) as LegalSection[];
  return (
    <article className="bg-section min-h-screen pt-32 pb-24">
      <Container className="max-w-4xl">
        <Link href="/" className={buttonStyles({ variant: "link" })}>
          <ArrowLeft className="size-4" />
          {t("back")}
        </Link>
        <div className="mt-9">
          <Badge className="border-amber-200 bg-amber-50 text-amber-800">
            <Scale className="size-3.5" />
            {t("draft")}
          </Badge>
          <h1 className="text-ink mt-5 text-4xl font-extrabold tracking-[-0.04em] sm:text-6xl">
            {t(`${kind}.title`)}
          </h1>
          <p className="text-muted mt-4 text-sm">{t("updated")}</p>
          <p className="text-secondary mt-7 max-w-2xl text-lg leading-8">
            {t(`${kind}.intro`)}
          </p>
        </div>
        <Card className="mt-10 divide-y divide-slate-100 px-5 sm:px-8">
          {sections.map((section) => (
            <section className="py-7" key={section.title}>
              <h2 className="text-ink text-xl font-bold">{section.title}</h2>
              <p className="text-secondary mt-3 leading-7">{section.text}</p>
            </section>
          ))}
        </Card>
        <p className="text-secondary mt-8 text-sm">
          {brand.primaryContactEmail}
        </p>
      </Container>
    </article>
  );
}
