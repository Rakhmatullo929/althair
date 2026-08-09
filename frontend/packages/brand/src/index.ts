export const brand = {
  name: process.env.NEXT_PUBLIC_BRAND_NAME ?? "AI Front Office",
  shortName: process.env.NEXT_PUBLIC_BRAND_SHORT_NAME ?? "AIFO",
  stage: "prelaunch",
  primaryContactEmail:
    process.env.NEXT_PUBLIC_CONTACT_EMAIL ?? "hello@example.com",
  telegramUrl: process.env.NEXT_PUBLIC_TELEGRAM_URL ?? "",
  appUrl: process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000",
} as const;

export type Brand = typeof brand;
