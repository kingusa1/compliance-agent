"use client";

import { useRouter } from "next/navigation";

import { useMe } from "@/lib/auth";
import { supabase } from "@/lib/supabase";
import { Button } from "@/components/ui/button";

/**
 * AccountTab — read-only email + role. Provides a Sign out action that
 * clears the Supabase session and redirects to /login.
 */
export function AccountTab() {
  const me = useMe();
  const router = useRouter();

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/login");
  }

  if (!me.data) return null;

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-[18px] font-semibold tracking-tight">Account</h2>
        <p className="mt-1 text-[13px] text-[var(--text-muted)]">
          Read-only profile info. Update your role via your Compliance admin.
        </p>
      </div>
      <div className="space-y-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-5">
        <div className="flex items-baseline justify-between">
          <span className="text-[13px] text-[var(--text-muted)]">Email</span>
          <span className="text-[13px] text-[var(--text-primary)]">{me.data.email}</span>
        </div>
        <div className="flex items-baseline justify-between">
          <span className="text-[13px] text-[var(--text-muted)]">Role</span>
          <span className="text-[13px] capitalize text-[var(--text-primary)]">{me.data.role}</span>
        </div>
      </div>
      <Button variant="destructive" onClick={signOut}>
        Sign out
      </Button>
    </div>
  );
}
