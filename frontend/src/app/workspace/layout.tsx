"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { IntelliProjectProvider } from "@/context/IntelliProjectContext";

const NAV: { href: string; label: string; kicker: string }[] = [
  {
    href: "/workspace",
    label: "Reasoning",
    kicker: "Trace, ask, diagnose",
  },
  {
    href: "/workspace/sequence",
    label: "Sequence",
    kicker: "State and transitions",
  },
  {
    href: "/workspace/project",
    label: "Project graph",
    kicker: "Objects and structure",
  },
];

export default function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

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
                Command layer
              </span>
            </span>
          </Link>

          <div className="mt-6 rounded-2xl border border-zinc-800/80 bg-zinc-900/45 p-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
              Product posture
            </p>
            <p className="mt-2 text-sm leading-5 text-zinc-300">
              Deterministic graph first. Runtime evidence second. AI wording
              only after INTELLI can show its work.
            </p>
          </div>

          <div className="mt-6">
            <p className="px-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">
              Workspace
            </p>
            <ul className="mt-2 flex flex-col gap-2">
              {NAV.map((item) => {
                const active =
                  item.href === "/workspace"
                    ? pathname === "/workspace"
                    : pathname.startsWith(item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={`block rounded-2xl border px-3 py-3 transition ${
                        active
                          ? "border-cyan-400/30 bg-cyan-400/10 text-white"
                          : "border-transparent text-zinc-400 hover:border-zinc-800 hover:bg-zinc-900/70 hover:text-zinc-100"
                      }`}
                    >
                      <span className="block text-sm font-semibold">
                        {item.label}
                      </span>
                      <span className="mt-0.5 block text-xs text-zinc-500">
                        {item.kicker}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className="mt-auto rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
              Build focus
            </p>
            <div className="mt-3 space-y-2 text-xs text-zinc-400">
              <p>Multi-vendor model</p>
              <p>Evidence-backed trace</p>
              <p>Runtime diagnosis</p>
            </div>
            <Link
              href="/"
              className="mt-4 inline-flex w-full items-center justify-center rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm font-medium text-zinc-300 transition hover:bg-zinc-900 hover:text-white"
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
