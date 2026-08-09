import { brand } from "@workspace/brand";
import type { MetadataRoute } from "next";
import { routing } from "@/i18n/routing";

export default function sitemap(): MetadataRoute.Sitemap {
  return routing.locales.map((locale) => ({
    url: `${brand.appUrl}/${locale}`,
    lastModified: new Date("2026-08-09"),
    changeFrequency: "weekly",
    priority: locale === "ru" ? 1 : 0.9,
    alternates: {
      languages: Object.fromEntries(
        routing.locales.map((item) => [item, `${brand.appUrl}/${item}`]),
      ),
    },
  }));
}
