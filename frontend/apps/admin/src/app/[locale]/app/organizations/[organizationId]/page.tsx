import { OrganizationInspector } from "@/components/organization-inspector";

export default async function OrganizationPage({
  params,
}: {
  params: Promise<{ organizationId: string }>;
}) {
  const { organizationId } = await params;
  return <OrganizationInspector organizationId={organizationId} />;
}
