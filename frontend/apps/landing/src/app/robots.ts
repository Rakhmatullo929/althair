import { brand } from "@workspace/brand";
import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: ["/api/"] },
    sitemap: `${brand.appUrl}/sitemap.xml`,
  };
}
