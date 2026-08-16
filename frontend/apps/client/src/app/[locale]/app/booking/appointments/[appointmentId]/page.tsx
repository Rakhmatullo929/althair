import { BookingPage } from "@/components/booking-page";

export default async function Page({
  params,
}: {
  params: Promise<{ appointmentId: string }>;
}) {
  const { appointmentId } = await params;
  return <BookingPage view="appointment" appointmentId={appointmentId} />;
}
