import { AdminShell } from "@/components/admin-shell";

export default function OperationsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AdminShell>{children}</AdminShell>;
}
