"use client";

import Link from "next/link";

import { Badge, Card, CardBody, CardHeader } from "@/components/intelli/ui";

const ADVANCED_ROUTES = [
  {
    href: "/workspace/sequence",
    label: "Sequence",
    description: "State candidates, transitions, and sequence semantics.",
  },
  {
    href: "/workspace/project",
    label: "Project graph",
    description: "Controllers, programs, version impact, and object structure.",
  },
];

export default function AdvancedWorkspacePage() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-6 py-8 lg:px-10">
      <div>
        <Badge tone="outline" uppercase>
          Advanced
        </Badge>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight text-white">
          Engineer surfaces
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
          Sequence analysis and project graph tools for deeper inspection. The
          main workspace focuses on import, tag search, and diagnosis.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {ADVANCED_ROUTES.map((route) => (
          <Link key={route.href} href={route.href} className="group">
            <Card className="h-full transition group-hover:border-cyan-400/30">
              <CardHeader eyebrow="Open" title={route.label} />
              <CardBody>
                <p className="text-sm leading-6 text-zinc-400">
                  {route.description}
                </p>
              </CardBody>
            </Card>
          </Link>
        ))}
      </div>

      <Link
        href="/workspace"
        className="text-sm text-zinc-500 underline-offset-4 hover:text-zinc-300 hover:underline"
      >
        Back to diagnosis workspace
      </Link>
    </div>
  );
}
