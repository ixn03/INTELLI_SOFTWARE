import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "INTELLI - Industrial Controls Intelligence",
  description:
    "Evidence-backed reasoning for PLC and DCS troubleshooting. Trace logic, evaluate runtime snapshots, and ask controls questions with deterministic support.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-[var(--background)] text-[var(--foreground)]">
        {children}
      </body>
    </html>
  );
}
