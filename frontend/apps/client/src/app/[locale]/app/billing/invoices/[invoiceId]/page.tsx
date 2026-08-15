import { BillingPage } from "@/components/billing-page";

export default async function Page({
  params,
}: {
  params: Promise<{ invoiceId: string }>;
}) {
  const { invoiceId } = await params;
  return <BillingPage view="invoice" invoiceId={invoiceId} />;
}
