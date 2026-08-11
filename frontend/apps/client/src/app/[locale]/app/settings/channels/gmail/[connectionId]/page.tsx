import { GmailConnectionPage } from "@/components/gmail-connection-page";

export default async function Page({
  params,
}: {
  params: Promise<{ connectionId: string }>;
}) {
  const { connectionId } = await params;
  return <GmailConnectionPage connectionId={connectionId} />;
}
