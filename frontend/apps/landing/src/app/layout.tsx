import { brand } from "@workspace/brand";
import type { Metadata } from "next";
import { Geologica, Manrope } from "next/font/google";
import { getLocale } from "next-intl/server";
import type { ReactNode } from "react";
import "./globals.css";

const manrope = Manrope({
  subsets: ["cyrillic", "latin"],
  variable: "--font-manrope",
  display: "swap",
});

const geologica = Geologica({
  subsets: ["cyrillic", "latin"],
  variable: "--font-geologica",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(brand.appUrl),
};

export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}) {
  const locale = await getLocale();
  return (
    <html lang={locale} className={`${manrope.variable} ${geologica.variable}`}>
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
