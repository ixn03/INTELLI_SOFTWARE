"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { IntelliProjectProvider } from "@/context/IntelliProjectContext";

const NAV: { href: string; label: string }[] = [
  { href: "/workspace", label: "Engineering" },
  { href: "/workspace/sequence", label: "Sequence / State" },
  { href: "/workspace/project", label: "Project" },
];

export default function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  return (
    <IntelliProjectProvider>
      <div className="flex min-h-screen bg-zinc-950 text-zinc-100">
        <nav className="flex w-52 shrink-0 flex-col border-r border-zinc-800/80 bg-zinc-950/95 py-8 pl-5 pr-3">
          <p className="mb-6 text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-500">
            Workspace
          </p>
          <ul className="flex flex-col gap-1">
            {NAV.map((item) => {
              const active =
                item.href === "/workspace"
                  ? pathname === "/workspace"
                  : pathname.startsWith(item.href);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className={`block rounded-lg px-3 py-2 text-sm transition ${
                      active
                        ? "bg-zinc-800/90 text-zinc-50"
                        : "text-zinc-400 hover:bg-zinc-900/80 hover:text-zinc-200"
                    }`}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
          <div className="mt-auto border-t border-zinc-800/80 pt-6">
            <Link
              href="/"
              className="block rounded-lg px-3 py-2 text-sm text-zinc-500 transition hover:bg-zinc-900/80 hover:text-zinc-300"
            >
              ← Home
            </Link>
          </div>
        </nav>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
      </div>
    </IntelliProjectProvider>
  );
}
