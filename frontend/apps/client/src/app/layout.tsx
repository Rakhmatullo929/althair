import type { Metadata } from "next";
import "./globals.css";
import { brand } from "@workspace/brand";

export const metadata: Metadata = {
  title: { default: `${brand.name} Portal`, template: `%s · ${brand.name}` },
  description:
    "Secure company onboarding and AI front-office configuration workspace.",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
