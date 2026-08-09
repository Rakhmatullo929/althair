import { AppShell } from "@/components/app-shell";
import { WorkspaceProvider } from "@/components/workspace-provider";

export default function PortalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <WorkspaceProvider>
      <AppShell>{children}</AppShell>
    </WorkspaceProvider>
  );
}
