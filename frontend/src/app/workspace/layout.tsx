"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { IntelliProjectProvider } from "@/context/IntelliProjectContext";

const MAIN_NAV = {
  href: "/workspace",
  label: "Diagnose",
  kicker: "Import, search, trace",
};

const INTEGRITY_NAV = [
  { href: "/workspace/integrity?tab=home", label: "Home" },
  { href: "/workspace/integrity?tab=plant", label: "Plant Explorer" },
  { href: "/workspace/integrity?tab=import", label: "Imports" },
  { href: "/workspace/integrity?tab=documents", label: "Documents" },
  { href: "/workspace/integrity?tab=reviews", label: "Review Queue" },
  { href: "/workspace/integrity?tab=templates", label: "Templates" },
  { href: "/workspace/integrity?tab=knowledge", label: "Knowledge Base" },
];

export default function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const onMainWorkspace =
    pathname === "/workspace" || pathname === "/workspace/";
  const onIntegrity = pathname.startsWith("/workspace/integrity");
  const onAdvanced = pathname.startsWith("/workspace/advanced");

  return (
    <IntelliProjectProvider>
      <div className="flex min-h-screen bg-[#050914] text-zinc-100">
        <nav className="hidden w-64 shrink-0 flex-col border-r border-white/10 bg-zinc-950/90 px-4 py-5 shadow-2xl shadow-black/30 xl:flex">
          <Link
            href="/"
            className="flex items-center gap-3 rounded-2xl border border-cyan-400/20 bg-cyan-400/10 px-3 py-3"
          >
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-cyan-300 font-mono text-sm font-bold text-cyan-950">
              IN
            </span>
            <span>
              <span className="block text-sm font-semibold text-white">
                INTELLI
              </span>
              <span className="block text-[10px] uppercase tracking-[0.2em] text-cyan-200/70">
                Controls workflow
              </span>
            </span>
          </Link>

          <div className="mt-6 rounded-2xl border border-zinc-800/80 bg-zinc-900/45 p-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
              How it works
            </p>
            <ol className="mt-3 space-y-2 text-xs leading-5 text-zinc-400">
              <li>
                <span className="font-medium text-zinc-300">1. Import</span> —
                upload a vendor export
              </li>
              <li>
                <span className="font-medium text-zinc-300">2. Discover</span> —
                identify plant and equipment
              </li>
              <li>
                <span className="font-medium text-zinc-300">3. Review</span> —
                approve document changes
              </li>
            </ol>
          </div>

          <div className="mt-6">
            <p className="px-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">
              Workspace
            </p>
            <ul className="mt-2 flex flex-col gap-2">
              <li>
                <Link
                  href={MAIN_NAV.href}
                  className={`block rounded-2xl border px-3 py-3 transition ${
                    onMainWorkspace
                      ? "border-cyan-400/30 bg-cyan-400/10 text-white"
                      : "border-transparent text-zinc-400 hover:border-zinc-800 hover:bg-zinc-900/70 hover:text-zinc-100"
                  }`}
                >
                  <span className="block text-sm font-semibold">
                    {MAIN_NAV.label}
                  </span>
                  <span className="mt-0.5 block text-xs text-zinc-500">
                    {MAIN_NAV.kicker}
                  </span>
                </Link>
              </li>
              <li>
                <div
                  className={`rounded-2xl border px-3 py-3 transition ${
                    onIntegrity
                      ? "border-cyan-400/30 bg-cyan-400/10 text-white"
                      : "border-transparent text-zinc-400"
                  }`}
                >
                  <Link href="/workspace/integrity?tab=home" className="block text-sm font-semibold">
                    Control Integrity
                  </Link>
                  <span className="mt-0.5 block text-xs text-zinc-500">
                    Import, discovery, review
                  </span>
                  <div className="mt-3 grid gap-1">
                    {INTEGRITY_NAV.map((item) => (
                      <Link
                        key={item.href}
                        href={item.href}
                        className="rounded-lg px-2 py-1.5 text-xs text-zinc-400 transition hover:bg-zinc-900/70 hover:text-zinc-100"
                      >
                        {item.label}
                      </Link>
                    ))}
                  </div>
                </div>
              </li>
            </ul>
          </div>

          <div className="mt-auto space-y-3">
            <Link
              href="/workspace/advanced"
              className={`block rounded-2xl border px-3 py-3 text-sm transition ${
                onAdvanced
                  ? "border-zinc-600 bg-zinc-900/70 text-zinc-200"
                  : "border-zinc-800/80 text-zinc-500 hover:border-zinc-700 hover:text-zinc-300"
              }`}
            >
              Advanced tools
            </Link>
            <Link
              href="/"
              className="inline-flex w-full items-center justify-center rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm font-medium text-zinc-300 transition hover:bg-zinc-900 hover:text-white"
            >
              Home
            </Link>
          </div>
        </nav>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
      </div>
    </IntelliProjectProvider>
  );
}
