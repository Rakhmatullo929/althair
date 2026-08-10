import { Suspense } from "react";
import { WebChatDemoPage } from "@/components/web-chat-demo-page";

export default function Page() {
  return (
    <Suspense>
      <WebChatDemoPage />
    </Suspense>
  );
}
