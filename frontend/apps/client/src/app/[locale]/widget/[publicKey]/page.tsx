import { WebChatWidget } from "@/components/web-chat-widget";

export default async function Page({
  params,
}: {
  params: Promise<{ locale: "ru" | "uz" | "en"; publicKey: string }>;
}) {
  const { locale, publicKey } = await params;
  return (
    <main id="main-content" className="webchat-standalone">
      <WebChatWidget publicKey={publicKey} initialLocale={locale} embedded />
    </main>
  );
}
