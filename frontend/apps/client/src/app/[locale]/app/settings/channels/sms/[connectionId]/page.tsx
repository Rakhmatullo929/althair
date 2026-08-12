import { SMSConnectionPage } from "@/components/sms-connection-page";

export default async function Page({
  params,
}: {
  params: Promise<{ connectionId: string }>;
}) {
  const { connectionId } = await params;
  return <SMSConnectionPage connectionId={connectionId} />;
}
