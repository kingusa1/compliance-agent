"use client";

/**
 * /login — ported pixel-perfect from
 * design/handoff-bundle/project/screens/login.jsx.
 *
 * 360px centered card on canvas bg + faint left rail showing the
 * compliance "C" gradient mark. 3 visible states: default, loading
 * (button spinner), error (red banner). Wires to Supabase auth.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Loader2, X as XIcon } from "lucide-react";

import { supabase } from "@/lib/supabase";
import { getMe } from "@/lib/api";
import { BrandMark } from "@/components/design/BrandMark";

const loginSchema = z.object({
  email: z.string().email("Enter a valid email address"),
  password: z.string().min(1, "Password is required"),
});

type LoginValues = z.infer<typeof loginSchema>;

export default function LoginPage() {
  const router = useRouter();
  const [authError, setAuthError] = useState<string | null>(null);
  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  async function onSubmit(values: LoginValues) {
    setAuthError(null);
    const { error } = await supabase.auth.signInWithPassword({
      email: values.email,
      password: values.password,
    });
    if (error) {
      setAuthError("Invalid credentials. Double-check your email and password.");
      return;
    }
    try {
      const me = await getMe();
      if (me.role === "reviewer") {
        router.replace("/queue");
      } else {
        // admin + lead land on the at-a-glance dashboard
        router.replace("/dashboard");
      }
    } catch {
      router.replace("/dashboard");
    }
  }

  const isSubmitting = form.formState.isSubmitting;
  const fieldErrors = form.formState.errors;

  return (
    <main
      style={{
        position: "relative",
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg-canvas)",
        color: "var(--text-primary)",
        fontFamily: "var(--font-sans)",
        letterSpacing: "-0.005em",
      }}
    >
      {/* faint left rail to imply chrome consistency, even pre-auth */}
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: 56,
          background: "var(--bg-elev1)",
          borderRight: "1px solid var(--border-subtle)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          padding: "12px 0",
        }}
      >
        <BrandMark size={24} />
      </div>

      {/* Card */}
      <form
        aria-labelledby="ca-login-heading"
        role="region"
        onSubmit={form.handleSubmit(onSubmit)}
        style={{
          width: 360,
          padding: 28,
          background: "var(--bg-elev1)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 12,
          display: "flex",
          flexDirection: "column",
          gap: 16,
          boxShadow: "var(--shadow-lg)",
        }}
      >
        {/* wordmark */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
          <BrandMark size={28} priority />
          <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>
            ComplianceAI
          </div>
        </div>

        <div>
          <h1
            id="ca-login-heading"
            style={{
              fontSize: 24,
              fontWeight: 600,
              letterSpacing: "-0.022em",
              margin: 0,
              color: "var(--text-primary)",
            }}
          >
            Sign in
          </h1>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>
            Welcome back. Enter your details below.
          </div>
        </div>

        {authError && (
          <div
            role="alert"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 8,
              padding: "10px 12px",
              background: "var(--red-bg)",
              border: "1px solid var(--red-border)",
              borderRadius: 6,
              color: "var(--red)",
              fontSize: 13,
            }}
          >
            <XIcon size={14} aria-hidden="true" style={{ marginTop: 2, flexShrink: 0 }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 500 }}>Invalid credentials</div>
              <div style={{ color: "rgba(239,68,68,0.75)", marginTop: 2, fontSize: 12 }}>
                Double-check your email and password.
              </div>
            </div>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <FieldShell label="Email" error={fieldErrors.email?.message}>
            <input
              {...form.register("email")}
              type="email"
              autoComplete="email"
              placeholder="you@company.com"
              style={inputStyle}
            />
          </FieldShell>
          <FieldShell label="Password" error={fieldErrors.password?.message}>
            <input
              {...form.register("password")}
              type="password"
              autoComplete="current-password"
              placeholder="Enter your password"
              style={inputStyle}
            />
          </FieldShell>
        </div>

        <button
          type="submit"
          disabled={isSubmitting}
          style={{
            height: 38,
            padding: "0 14px",
            fontSize: 14,
            fontWeight: 500,
            letterSpacing: "-0.005em",
            background: "var(--emerald)",
            color: "#04201a",
            border: "1px solid var(--emerald)",
            borderRadius: 8,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            cursor: isSubmitting ? "not-allowed" : "pointer",
            opacity: isSubmitting ? 0.7 : 1,
            fontFamily: "inherit",
            boxShadow: "var(--shadow-sm)",
            marginTop: 4,
          }}
        >
          {isSubmitting ? (
            <>
              <Loader2 size={16} style={{ animation: "ca-spin 0.7s linear infinite" }} />
              Signing in…
            </>
          ) : (
            "Sign in"
          )}
        </button>

        <div style={{ fontSize: 13, color: "var(--text-muted)", textAlign: "center", marginTop: 4 }}>
          Need help?{" "}
          <span
            style={{
              color: "var(--text-primary)",
              cursor: "pointer",
              textDecoration: "underline",
              textUnderlineOffset: 2,
              textDecorationColor: "var(--border-strong)",
            }}
          >
            Contact your admin
          </span>
        </div>
      </form>

      <footer style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 24 }}>
        © 2026 ComplianceAI · v3.0.0
      </footer>
    </main>
  );
}

const inputStyle: React.CSSProperties = {
  height: 32,
  padding: "0 10px",
  background: "var(--bg-elev2)",
  border: "1px solid var(--border-subtle)",
  borderRadius: 6,
  color: "var(--text-primary)",
  fontSize: 13,
  width: "100%",
  outline: "none",
  fontFamily: "inherit",
  letterSpacing: "-0.003em",
};

function FieldShell({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "block" }}>
      <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 6 }}>{label}</div>
      {children}
      {error && (
        <div style={{ fontSize: 11, color: "var(--red)", marginTop: 4 }}>{error}</div>
      )}
    </label>
  );
}
