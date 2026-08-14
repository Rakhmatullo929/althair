import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Althair Operations", template: "%s · Althair Operations" },
  description:
    "Secure internal operations control plane for authorized Althair platform staff.",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
