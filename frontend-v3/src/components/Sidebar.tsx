"use client";

/**
 * Global sidebar rail — ported pixel-perfect from
 * design/handoff-bundle/project/hifi/tokens-hifi.jsx HFSidebar.
 *
 * Always visible on every authenticated page (mounted in (reviewer)
 * and (admin) layouts). 56px collapsed → 220px expanded on hover.
 * Bottom user pill shows "<email> · <role>".
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment, useState } from "react";
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

  // ── System — configuration ────────────────────────────────────
  { key: "settings",      label: "Settings",      icon: SettingsIcon, href: "/settings",       roles: ["admin", "lead"], section: "System" },
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

export function Sidebar() {
  const [hover, setHover] = useState(false);
  const path = usePathname() || "/";
  const { session } = useAuth();
  const me = useMe();
  const role = me.data?.role ?? "reviewer";
  const email = me.data?.email ?? session?.user?.email ?? "you@example.com";
  const items = NAV_ITEMS.filter((it) => it.roles.includes(role));
  const active = activeKey(path);
  const queueBadge = role === "reviewer" || role === "lead" || role === "admin" ? null : null;

  const expanded = hover;
  const width = expanded ? 220 : 56;

  return (
    <aside
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
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
      {/* Logo */}
      <div
        style={{
          height: 32,
          display: "flex",
          alignItems: "center",
          padding: "0 16px",
          marginBottom: 16,
          gap: 10,
        }}
      >
        <BrandMark size={24} priority />

        {expanded && (
          <div
            style={{
              fontSize: 14,
              fontWeight: 600,
              letterSpacing: "-0.018em",
              whiteSpace: "nowrap",
              color: "var(--text-primary)",
            }}
          >
            ComplianceAI
          </div>
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
              {expanded && item.badgeKey === "queue" && queueBadge != null && (
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    fontVariantNumeric: "tabular-nums",
                    padding: "1px 6px",
                    background: isActive ? "var(--emerald-bg-strong)" : "var(--bg-elev3)",
                    color: isActive ? "var(--emerald-400)" : "var(--text-muted)",
                    borderRadius: 999,
                  }}
                >
                  {queueBadge}
                </span>
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
