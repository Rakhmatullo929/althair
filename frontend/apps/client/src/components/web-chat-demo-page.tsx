"use client";

import { useLocale } from "next-intl";
import { useSearchParams } from "next/navigation";
import { WebChatWidget } from "./web-chat-widget";

export function WebChatDemoPage() {
  const locale = useLocale() as "ru" | "uz" | "en";
  const search = useSearchParams();
  const publicKey =
    search.get("installation") ?? process.env.NEXT_PUBLIC_WEB_CHAT_DEMO_KEY;
  if (!publicKey)
    return (
      <main id="main-content" className="webchat-center">
        <h1>Public Web Chat demo</h1>
        <p>The controlled demo is not configured in this environment.</p>
      </main>
    );
  return (
    <main id="main-content" className="webchat-demo">
      <section className="webchat-demo-copy">
        <span>DEVELOPMENT DEMO</span>
        <h1>Public Web Chat</h1>
        <p>
          This isolated test page uses real organization-scoped CRM records and
          the deterministic CI provider. It is not the Landing application.
        </p>
        <dl>
          <div>
            <dt>Transport</dt>
            <dd>SSE with polling fallback</dd>
          </div>
          <div>
            <dt>Isolation</dt>
            <dd>iframe + opaque visitor session</dd>
          </div>
          <div>
            <dt>Languages</dt>
            <dd>RU · UZ · EN</dd>
          </div>
        </dl>
      </section>
      <div className="webchat-demo-frame">
        <WebChatWidget publicKey={publicKey} initialLocale={locale} />
      </div>
    </main>
  );
}
