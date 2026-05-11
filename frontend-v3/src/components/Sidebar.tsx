"use client";

/**
 * Global sidebar rail.
 *
 * Default-expanded (220px) so labels are always visible — the
 * icon-only collapsed mode is a power-user toggle, not the default,
 * because new users could not tell what each of 13 unlabelled icons
 * meant (audit-late 2026-05-10 UX1).
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment, useEffect, useState } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { useQuery } from "@tanstack/react-query";
import { fetchQueue } from "@/lib/queries/reviewer";
import {
  Inbox,
  Users,
  ListChecks,
  BarChart3,
  Settings as SettingsIcon,
  Briefcase,
  ShieldCheck,
  ShieldAlert,
  Activity,
  AlertTriangle,
  Send,
  Table as TableIcon,
  LayoutDashboard,
  BookOpen,
  type LucideIcon,
} from "lucide-react";

import { useAuth, useMe } from "@/lib/auth";
import { signOut } from "@/lib/supabase";
import { BrandMark } from "@/components/design/BrandMark";

type NavItem = {
  key: string;
  label: string;
  icon: LucideIcon;
  href: string;
  roles: Array<"reviewer" | "lead" | "admin">;
  badgeKey?: "queue";
  /** Section header to show ABOVE this item when expanded. */
  section?: "Work" | "Catalogue" | "Audit" | "System";
};

// Items are grouped by section so the rail tells the user *what* to do
// (Work), *what to manage* (Catalogue), *what to verify* (Audit), and
// *where to configure* (System). The Dashboard sits above any section.
const NAV_ITEMS: NavItem[] = [
  { key: "dashboard",     label: "Dashboard",     icon: LayoutDashboard, href: "/dashboard",  roles: ["reviewer", "lead", "admin"] },

  // ── Work — daily review surface ───────────────────────────────
  { key: "queue",         label: "Review Queue",  icon: Inbox,        href: "/queue",          roles: ["reviewer", "lead", "admin"], badgeKey: "queue", section: "Work" },
  { key: "tracker",       label: "Tracker",       icon: TableIcon,    href: "/tracker",        roles: ["admin", "lead"] },
  { key: "rejections",    label: "Rejections",    icon: AlertTriangle, href: "/rejections",    roles: ["admin", "lead"] },

  // ── Catalogue — operational data ──────────────────────────────
  { key: "customers",     label: "Customers",     icon: Users,        href: "/customers",      roles: ["admin", "lead"], section: "Catalogue" },
  { key: "deals",         label: "Deals",         icon: Briefcase,    href: "/deals",          roles: ["admin", "lead"] },
  { key: "agents",        label: "Agents",        icon: BarChart3,    href: "/agents",         roles: ["admin", "lead"] },
  { key: "scripts",       label: "Scripts",       icon: ListChecks,   href: "/scripts",        roles: ["admin", "lead"] },

  // ── Audit — verdict trails ────────────────────────────────────
  { key: "compliant",     label: "Compliant",     icon: ShieldCheck,  href: "/compliant",      roles: ["admin", "lead"], section: "Audit" },
  { key: "non-compliant", label: "Non-compliant", icon: ShieldAlert,  href: "/non-compliant",  roles: ["admin", "lead"] },
  { key: "observability", label: "Observability", icon: Activity,     href: "/observability",  roles: ["admin", "lead"] },

  // ── System — configuration + reference ──────────────────────
  { key: "settings",      label: "Settings",      icon: SettingsIcon, href: "/settings",       roles: ["admin", "lead"], section: "System" },
  { key: "guide",         label: "User Guide",    icon: BookOpen,     href: "/guide",          roles: ["reviewer", "lead", "admin"] },
];

function activeKey(path: string): string {
  if (path === "/" || path === "") return "dashboard";
  // /calls/[id] is a reviewer-deep link, but the sidebar should still highlight Queue when reviewing.
  if (path.startsWith("/calls/") && path !== "/calls") return "queue";
  for (const item of NAV_ITEMS) {
    if (path === item.href) return item.key;
    if (path.startsWith(item.href + "/")) return item.key;
  }
  return "dashboard";
}

function initialsOf(email: string | undefined): string {
  if (!email) return "S";
  const local = email.split("@")[0] || "";
  const parts = local.split(/[._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (local.slice(0, 2) || "S").toUpperCase();
}

const SIDEBAR_PREF_KEY = "ca:sidebar:collapsed";

export function Sidebar() {
  const [collapsed, setCollapsed] = useState<boolean>(false);

  // Persist collapsed pref so it survives navigation. Default = expanded.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const v = window.localStorage.getItem(SIDEBAR_PREF_KEY);
      if (v === "1") setCollapsed(true);
    } catch {
      // ignore
    }
  }, []);
  const toggleCollapsed = () => {
    setCollapsed((v) => {
      const next = !v;
      try { window.localStorage.setItem(SIDEBAR_PREF_KEY, next ? "1" : "0"); } catch { /* ignore */ }
      return next;
    });
  };

  const path = usePathname() || "/";
  const { session } = useAuth();
  const me = useMe();
  const role = me.data?.role ?? "reviewer";
  const email = me.data?.email ?? session?.user?.email ?? "you@example.com";
  const items = NAV_ITEMS.filter((it) => it.roles.includes(role));
  const active = activeKey(path);

  // Live queue count badge — refreshes every 30s, like the queue page itself.
  const queueQ = useQuery({
    queryKey: ["sidebar", "queue-backlog"],
    queryFn: () => fetchQueue("unclaimed"),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
  const queueBadge = queueQ.data?.metrics?.backlog ?? null;

  const expanded = !collapsed;
  const width = expanded ? 220 : 60;

  return (
    <aside
      style={{
        width,
        flexShrink: 0,
        height: "100vh",
        background: "var(--bg-elev1)",
        borderRight: "1px solid var(--border-subtle)",
        transition: "width 140ms ease",
        display: "flex",
        flexDirection: "column",
        padding: "12px 0",
        overflow: "hidden",
        position: "sticky",
        top: 0,
        zIndex: 30,
      }}
    >
      {/* Logo + collapse toggle */}
      <div
        style={{
          height: 32,
          display: "flex",
          alignItems: "center",
          padding: "0 12px",
          marginBottom: 16,
          gap: 10,
        }}
      >
        <BrandMark size={24} priority />

        {expanded && (
          <>
            <div
              style={{
                fontSize: 14,
                fontWeight: 600,
                letterSpacing: "-0.018em",
                whiteSpace: "nowrap",
                color: "var(--text-primary)",
                flex: 1,
              }}
            >
              ComplianceAI
            </div>
            <button
              type="button"
              onClick={toggleCollapsed}
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: 24,
                height: 24,
                borderRadius: 4,
                background: "transparent",
                border: "none",
                color: "var(--text-muted)",
                cursor: "pointer",
              }}
            >
              <PanelLeftClose size={14} />
            </button>
          </>
        )}
        {!expanded && (
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label="Expand sidebar"
            title="Expand sidebar"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 24,
              height: 24,
              borderRadius: 4,
              background: "transparent",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              marginLeft: -4,
            }}
          >
            <PanelLeftOpen size={14} />
          </button>
        )}
      </div>

      {/* Nav */}
      <nav
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          padding: "0 8px",
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
        }}
        className="ca-scroll"
      >
        {items.map((item) => {
          const isActive = item.key === active;
          const Icon = item.icon;
          const sectionHeader = expanded && item.section ? (
            <div
              style={{
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                color: "var(--text-dim)",
                padding: "10px 10px 4px",
              }}
            >
              {item.section}
            </div>
          ) : null;
          return (
            <Fragment key={item.key}>
            {sectionHeader}
            <Link
              href={item.href}
              title={!expanded ? item.label : undefined}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                height: 32,
                padding: "0 10px",
                borderRadius: 6,
                color: isActive ? "var(--text-primary)" : "var(--text-muted)",
                background: isActive ? "var(--bg-elev3)" : "transparent",
                fontSize: 13,
                fontWeight: 500,
                letterSpacing: "-0.003em",
                textDecoration: "none",
                whiteSpace: "nowrap",
                position: "relative",
              }}
            >
              <span
                style={{
                  display: "flex",
                  flexShrink: 0,
                  color: isActive ? "var(--emerald-400)" : "var(--text-dim)",
                }}
              >
                <Icon size={16} strokeWidth={1.75} />
              </span>
              {expanded && <span style={{ flex: 1 }}>{item.label}</span>}
              {expanded && item.badgeKey === "queue" && queueBadge != null && queueBadge > 0 && (
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    fontVariantNumeric: "tabular-nums",
                    padding: "1px 6px",
                    background: "var(--amber-bg)",
                    color: "var(--amber)",
                    borderRadius: 999,
                  }}
                >
                  {queueBadge}
                </span>
              )}
              {!expanded && item.badgeKey === "queue" && queueBadge != null && queueBadge > 0 && (
                <span
                  style={{
                    position: "absolute",
                    top: 4,
                    right: 6,
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: "var(--amber)",
                  }}
                />
              )}
            </Link>
            </Fragment>
          );
        })}
      </nav>

      {/* User pill */}
      <div
        style={{
          margin: "0 8px",
          padding: "10px 8px",
          borderTop: "1px solid var(--border-subtle)",
          display: "flex",
          alignItems: "center",
          gap: 10,
          cursor: "pointer",
        }}
        title={expanded ? "Sign out" : email}
        onClick={() => {
          // Click area also acts as a sign-out shortcut on the user chip.
          void signOut().then(() => {
            if (typeof window !== "undefined") window.location.href = "/login";
          });
        }}
      >
        <div
          style={{
            width: 28,
            height: 28,
            borderRadius: "50%",
            background: "var(--emerald-bg-strong)",
            border: "1px solid var(--emerald-border)",
            color: "var(--emerald-400)",
            display: "grid",
            placeItems: "center",
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: "0.02em",
            flexShrink: 0,
          }}
        >
          {initialsOf(email)}
        </div>
        {expanded && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              minWidth: 0,
              flex: 1,
            }}
          >
            <div
              style={{
                fontSize: 12,
                fontWeight: 500,
                color: "var(--text-primary)",
                letterSpacing: "-0.005em",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {email}
            </div>
            <div
              style={{
                fontSize: 10.5,
                color: "var(--text-muted)",
                display: "flex",
                alignItems: "center",
                gap: 4,
                marginTop: 1,
              }}
            >
              <span
                style={{
                  display: "inline-block",
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: "var(--emerald)",
                  boxShadow: "0 0 0 2px rgba(16,185,129,0.18)",
                }}
              />
              {role}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
