import Link from "next/link";
import { ArrowRight, Home, Inbox, LayoutDashboard, BookOpen } from "lucide-react";
import { BrandMark } from "@/components/design/BrandMark";

export const metadata = {
  title: "Page not found · Compliance Agent",
  robots: { index: false, follow: false },
};

const QUICK_LINKS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, description: "KPI strip + navigator" },
  { href: "/queue", label: "Review Queue", icon: Inbox, description: "Calls awaiting human sign-off" },
  { href: "/guide", label: "User Guide", icon: BookOpen, description: "Step-by-step manual" },
];

export default function NotFound() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-[var(--bg-canvas)] px-6 py-12">
      <div className="w-full max-w-md">
        <div className="flex items-center gap-3 mb-8">
          <BrandMark size={32} priority />
          <span className="text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
            ComplianceAI
          </span>
        </div>

        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-6 mb-6">
          <div className="text-[11px] uppercase tracking-wide text-[var(--text-muted)] mb-1">
            Error 404
          </div>
          <h1 className="text-[22px] font-semibold tracking-tight text-[var(--text-primary)] mb-2">
            That page doesn&apos;t exist
          </h1>
          <p className="text-[13px] leading-relaxed text-[var(--text-muted)]">
            The link you followed may be outdated, or the page may have been moved.
            Try one of the destinations below — or head back to the Dashboard.
          </p>
          <Link
            href="/dashboard"
            className="mt-5 inline-flex items-center gap-2 rounded-md bg-emerald-600 px-3.5 py-2 text-[12.5px] font-medium text-white hover:bg-emerald-700"
          >
            <Home className="size-3.5" /> Go to Dashboard
          </Link>
        </div>

        <div className="space-y-2">
          {QUICK_LINKS.map((link) => {
            const Icon = link.icon;
            return (
              <Link
                key={link.href}
                href={link.href}
                className="group flex items-center gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3 hover:border-[var(--border-strong)] hover:bg-[var(--bg-elev2)] transition-colors"
              >
                <div className="grid size-8 place-items-center rounded-md bg-[var(--bg-elev3)]">
                  <Icon className="size-4 text-[var(--emerald-400)]" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium text-[var(--text-primary)]">
                    {link.label}
                  </div>
                  <div className="text-[11.5px] text-[var(--text-muted)] truncate">
                    {link.description}
                  </div>
                </div>
                <ArrowRight className="size-4 text-[var(--text-dim)] transition-transform group-hover:translate-x-0.5 group-hover:text-[var(--text-muted)]" />
              </Link>
            );
          })}
        </div>

        <p className="mt-6 text-center text-[11.5px] text-[var(--text-dim)]">
          Compliance Agent · Watt Utilities · Opus 4.7
        </p>
      </div>
    </div>
  );
}
