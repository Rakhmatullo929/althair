import { PublicBookingPage } from "@/components/public-booking-page";

export default async function Page({
  params,
}: {
  params: Promise<{ publicKey: string }>;
}) {
  const { publicKey } = await params;
  return <PublicBookingPage publicKey={publicKey} />;
}
