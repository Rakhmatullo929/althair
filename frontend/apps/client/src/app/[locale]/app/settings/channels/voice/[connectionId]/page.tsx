import { VoiceConnectionPage } from "@/components/voice-connection-page";

export default async function Page({
  params,
}: {
  params: Promise<{ connectionId: string }>;
}) {
  const { connectionId } = await params;
  return <VoiceConnectionPage connectionId={connectionId} />;
}
