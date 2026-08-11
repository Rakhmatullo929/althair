import { InstagramConnectionPage } from "@/components/instagram-connection-page";

export default async function Page({
  params,
}: {
  params: Promise<{ connectionId: string }>;
}) {
  const { connectionId } = await params;
  return <InstagramConnectionPage connectionId={connectionId} />;
}
