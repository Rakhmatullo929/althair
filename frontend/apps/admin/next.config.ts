import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");
const backendApiOrigin = process.env.BACKEND_API_ORIGIN?.replace(/\/$/, "");

export default withNextIntl({
  devIndicators: false,
  skipTrailingSlashRedirect: true,
  async rewrites() {
    if (!backendApiOrigin) return [];
    return [
      { source: "/api/:path*", destination: `${backendApiOrigin}/api/:path*/` },
    ];
  },
});
