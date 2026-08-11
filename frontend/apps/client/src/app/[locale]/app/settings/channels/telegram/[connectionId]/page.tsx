import { TelegramConnectionPage } from "@/components/telegram-connection-page";

export default async function Page({
  params,
}: {
  params: Promise<{ connectionId: string }>;
}) {
  const { connectionId } = await params;
  return <TelegramConnectionPage connectionId={connectionId} />;
}
