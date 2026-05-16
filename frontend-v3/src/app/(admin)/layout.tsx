import { AuthGuard } from "@/lib/auth";
import { ScreenFrame } from "@/components/design/ScreenFrame";

/**
 * Admin route-group layout. Wraps admin pages with AuthGuard +
 * the global ScreenFrame (persistent Sidebar rail).
 */
export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGuard allowedRoles={["admin", "lead"]}>
      <ScreenFrame>{children}</ScreenFrame>
    </AuthGuard>
  );
}
