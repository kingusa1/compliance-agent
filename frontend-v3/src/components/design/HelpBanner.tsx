"use client";

/**
 * Reusable inline-help banner.
 *
 * Shows a one-paragraph "what is this page / how do I use it" hint at the
 * top of every operational page so reviewers don't have to bounce out to
 * /guide for the basics. Each banner is dismissible (state stored in
 * localStorage) so power-users can hide it after they've read it once.
 */
import { useEffect, useState } from "react";
import { Info, X, ArrowRight } from "lucide-react";
import Link from "next/link";

export interface HelpBannerProps {
  /** Stable key — used as the localStorage hide flag. */
  id: string;
  /** Headline above the body text. Keep it 4-7 words. */
  title: string;
  /** Body — 1-2 short sentences explaining the page. */
  children: React.ReactNode;
  /** Optional learn-more link (typically into /guide#section). */
  href?: string;
  hrefLabel?: string;
}

export function HelpBanner({ id, title, children, href, hrefLabel = "Read the full guide" }: HelpBannerProps) {
  const storageKey = `help-banner-hidden:${id}`;
  const [hidden, setHidden] = useState<boolean>(true); // start hidden to avoid SSR mismatch

  useEffect(() => {
    if (typeof window === "undefined") return;
    setHidden(window.localStorage.getItem(storageKey) === "1");
  }, [storageKey]);

  if (hidden) return null;

  return (
    <div
      className="mx-6 my-3 flex items-start gap-3 rounded-lg border border-[var(--emerald-border)] bg-[var(--emerald-bg)]/40 px-4 py-3"
      role="note"
      aria-label="Help"
    >
      <Info className="mt-0.5 size-4 shrink-0 text-emerald-300" />
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] font-semibold text-emerald-200">{title}</div>
        <div className="mt-1 text-[12px] leading-relaxed text-emerald-100/85">
          {children}
        </div>
        {href ? (
          <Link
            href={href}
            className="mt-2 inline-flex items-center gap-1 text-[11.5px] text-emerald-300 hover:text-emerald-200"
          >
            {hrefLabel} <ArrowRight className="size-3" />
          </Link>
        ) : null}
      </div>
      <button
        type="button"
        aria-label="Hide help"
        onClick={() => {
          if (typeof window !== "undefined") {
            window.localStorage.setItem(storageKey, "1");
          }
          setHidden(true);
        }}
        className="rounded p-1 text-emerald-300 hover:bg-[var(--emerald-bg)] hover:text-emerald-200"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}
