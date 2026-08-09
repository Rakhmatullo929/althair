import { InvitationForm } from "@/components/auth-forms";

export default async function AcceptInvitationPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return <InvitationForm token={token} />;
}
