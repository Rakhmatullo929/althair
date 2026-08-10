import { WebChatInstallationPage } from "@/components/web-chat-installation-page";

export default async function Page({
  params,
}: {
  params: Promise<{ installationId: string }>;
}) {
  const { installationId } = await params;
  return <WebChatInstallationPage installationId={installationId} />;
}
