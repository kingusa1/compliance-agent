import type { Metadata } from "next";
import { Inter_Tight, JetBrains_Mono } from "next/font/google";

import "./globals.css";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { ThemeProvider } from "@/components/providers/ThemeProvider";
import { Toaster } from "@/components/ui/sonner";

const interTight = Inter_Tight({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

const jetBrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "ComplianceAI",
  description: "Compliance review for sales calls",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`dark ${interTight.variable} ${jetBrainsMono.variable} h-full antialiased`}
    >
      <head>
        {/* Pre-paint density read so tables render at the chosen
            row-padding immediately, no flash. Source of truth = Settings →
            Density tab. See globals.css `html[data-density="..."]`. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var d=localStorage.getItem('v3:density');" +
              "if(d==='compact'||d==='comfortable'||d==='spacious')" +
              "document.documentElement.setAttribute('data-density',d);}catch(e){}})();",
          }}
        />
      </head>
      <body className="min-h-full flex flex-col bg-[var(--bg-canvas)] text-[var(--text-primary)]">
        <QueryProvider>
          <ThemeProvider>
            {children}
            <Toaster richColors position="top-right" />
          </ThemeProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
