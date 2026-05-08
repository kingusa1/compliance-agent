import { AuthGuard } from "@/lib/auth";
import { ScreenFrame } from "@/components/design/ScreenFrame";

/**
 * Reviewer route-group layout. Wraps reviewer pages with AuthGuard +
 * the global ScreenFrame (which mounts the persistent Sidebar rail
 * required by the design spec).
 */
export default function ReviewerLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGuard allowedRoles={["reviewer", "lead", "admin"]}>
      <ScreenFrame>{children}</ScreenFrame>
    </AuthGuard>
  );
}
