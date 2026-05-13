"use client";

/**
 * Small, reusable visual primitives for the INTELLI shell.
 *
 * Kept in one file on purpose: every component here is a thin
 * Tailwind-based shell with no internal state, so co-locating them
 * makes the design language easy to scan and update. Anything that
 * grows logic of its own should graduate to its own module.
 *
 * Tone:
 *   - matte zinc/slate surfaces
 *   - hairline borders
 *   - small uppercase eyebrow labels for sections
 *   - confidence / status badges in the standard four colors
 *
 * No third-party UI dependency.
 */

import { type KeyboardEvent, ReactNode, useState } from "react";

import type { ConfidenceLevel } from "@/types/reasoning";

// ===========================================================================
// Card -- the standard surface for every section in the layout.
// ===========================================================================

export function Card({
  children,
  className = "",
  as: As = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: keyof React.JSX.IntrinsicElements;
}) {
  return (
    <As
      className={`rounded-2xl border border-zinc-800/80 bg-zinc-900/50 ${className}`}
    >
      {children}
    </As>
  );
}

export function CardHeader({
  title,
  eyebrow,
  trailing,
}: {
  title: ReactNode;
  eyebrow?: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-zinc-800/70 px-5 py-3">
      <div>
        {eyebrow ? (
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
            {eyebrow}
          </p>
        ) : null}
        <h3 className="mt-0.5 text-sm font-medium text-zinc-100">{title}</h3>
      </div>
      {trailing}
    </div>
  );
}

export function CardBody({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`p-5 ${className}`}>{children}</div>;
}

// ===========================================================================
// Eyebrow -- inline section eyebrow used inside a card or sidebar block.
// ===========================================================================

export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
      {children}
    </p>
  );
}

// ===========================================================================
// Button -- consistent button styling. `tone` controls the role.
// ===========================================================================

type ButtonTone = "primary" | "secondary" | "ghost";

export function Button({
  children,
  onClick,
  disabled,
  type = "button",
  tone = "primary",
  className = "",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit" | "reset";
  tone?: ButtonTone;
  className?: string;
  title?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-lg px-3.5 py-2 " +
    "text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40";
  const tones: Record<ButtonTone, string> = {
    primary:
      "bg-zinc-100 text-zinc-900 hover:bg-white shadow-sm shadow-black/10",
    secondary:
      "border border-zinc-700 bg-zinc-800/70 text-zinc-100 hover:bg-zinc-700/70",
    ghost: "text-zinc-300 hover:bg-zinc-800/60",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`${base} ${tones[tone]} ${className}`}
    >
      {children}
    </button>
  );
}

// ===========================================================================
// TextInput -- text input matched to the dark shell.
// ===========================================================================

export function TextInput({
  value,
  onChange,
  placeholder,
  ariaLabel,
  type = "text",
  className = "",
  onKeyDown,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  type?: string;
  className?: string;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
}) {
  return (
    <input
      type={type}
      value={value}
      aria-label={ariaLabel}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
      placeholder={placeholder}
      className={`w-full rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-600 ${className}`}
    />
  );
}

export function TextArea({
  value,
  onChange,
  placeholder,
  ariaLabel,
  rows = 4,
  className = "",
  onKeyDown,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  rows?: number;
  className?: string;
  onKeyDown?: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
}) {
  return (
    <textarea
      value={value}
      aria-label={ariaLabel}
      rows={rows}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
      placeholder={placeholder}
      className={`min-h-[5.5rem] w-full resize-y rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2.5 text-sm leading-relaxed text-zinc-100 placeholder:text-zinc-500 focus:border-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-600 ${className}`}
    />
  );
}

// ===========================================================================
// Badge -- compact chip used for object types, instruction types,
// confidence levels, intent tags, etc.
// ===========================================================================

type BadgeTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "outline";

export function Badge({
  children,
  tone = "neutral",
  className = "",
  uppercase = false,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  className?: string;
  uppercase?: boolean;
}) {
  const tones: Record<BadgeTone, string> = {
    neutral: "border-zinc-700/70 bg-zinc-800/70 text-zinc-200",
    info: "border-sky-800/60 bg-sky-950/40 text-sky-200",
    success: "border-emerald-800/60 bg-emerald-950/40 text-emerald-200",
    warning: "border-amber-800/60 bg-amber-950/40 text-amber-200",
    danger: "border-rose-800/60 bg-rose-950/40 text-rose-200",
    outline: "border-zinc-700/70 bg-transparent text-zinc-300",
  };
  return (
    <span
      className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium tracking-wide ${
        uppercase ? "uppercase tracking-[0.12em]" : ""
      } ${tones[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

// ===========================================================================
// ConfidenceBadge -- maps backend confidence levels to standard tones.
// ===========================================================================

export function ConfidenceBadge({
  value,
}: {
  value: ConfidenceLevel | undefined;
}) {
  if (!value) return null;
  const tone: BadgeTone =
    value === "high" || value === "very_high"
      ? "success"
      : value === "medium"
        ? "warning"
        : value === "unknown"
          ? "neutral"
          : "danger";
  return (
    <Badge tone={tone} uppercase>
      {value.replace("_", " ")}
    </Badge>
  );
}

// ===========================================================================
// Stat -- value + label inline chip. Used for counts.
// ===========================================================================

export function Stat({ value, label }: { value: number | string; label: string }) {
  return (
    <span className="inline-flex items-baseline gap-1 rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1 text-xs">
      <span className="font-mono text-sm text-zinc-100">{value}</span>
      <span className="text-zinc-500">{label}</span>
    </span>
  );
}

// ===========================================================================
// EmptyState -- consistent placeholder when a panel has no content.
// ===========================================================================

export function EmptyState({
  title,
  hint,
  className = "",
}: {
  title: string;
  hint?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col items-start gap-1 rounded-xl border border-dashed border-zinc-800 bg-zinc-900/30 px-4 py-5 ${className}`}
    >
      <p className="text-sm text-zinc-300">{title}</p>
      {hint ? <p className="text-xs text-zinc-500">{hint}</p> : null}
    </div>
  );
}

// ===========================================================================
// InlineError -- subtle inline error banner.
// ===========================================================================

export function InlineError({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-rose-900/70 bg-rose-950/40 px-3 py-2 text-sm text-rose-100">
      {children}
    </p>
  );
}

// ===========================================================================
// LoadingLine -- low-contrast loading shim with subtle pulse.
// ===========================================================================

export function LoadingLine({ children }: { children: ReactNode }) {
  return (
    <p className="animate-pulse text-sm text-zinc-400">{children}</p>
  );
}

// ===========================================================================
// Accordion -- minimal disclosure widget. ``defaultOpen`` controls
// initial state. Animation is intentionally absent to keep things
// snappy in the industrial-app spirit.
// ===========================================================================

export function Accordion({
  title,
  count,
  defaultOpen = false,
  children,
  eyebrow,
}: {
  title: ReactNode;
  count?: number | string | null;
  defaultOpen?: boolean;
  children: ReactNode;
  eyebrow?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="overflow-hidden rounded-xl border border-zinc-800/80 bg-zinc-900/40">
      <button
        type="button"
        onClick={() => setOpen((s) => !s)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-zinc-800/40"
      >
        <span className="flex items-baseline gap-2">
          {eyebrow ? (
            <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
              {eyebrow}
            </span>
          ) : null}
          <span className="text-sm font-medium text-zinc-100">{title}</span>
          {count !== undefined && count !== null ? (
            <span className="text-xs text-zinc-500">({count})</span>
          ) : null}
        </span>
        <Chevron open={open} />
      </button>
      {open ? (
        <div className="border-t border-zinc-800/70 bg-zinc-950/30 p-4">
          {children}
        </div>
      ) : null}
    </div>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      className={`shrink-0 text-zinc-500 transition-transform ${open ? "rotate-90" : ""}`}
      aria-hidden="true"
    >
      <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// ===========================================================================
// KVRow -- key/value row with optional mono styling, used inside
// relationship detail panels.
// ===========================================================================

export function KVRow({
  k,
  v,
  mono = false,
  breakAll = false,
}: {
  k: string;
  v: ReactNode;
  mono?: boolean;
  breakAll?: boolean;
}) {
  return (
    <div className="flex gap-3 py-1 text-xs">
      <span className="w-28 shrink-0 text-zinc-500">{k}</span>
      <span
        className={`text-zinc-200 ${mono ? "font-mono" : ""} ${
          breakAll ? "break-all" : ""
        }`}
      >
        {v}
      </span>
    </div>
  );
}

// ===========================================================================
// Code -- monospaced code block. Used for JSON dumps.
// ===========================================================================

export function Code({ children }: { children: ReactNode }) {
  return (
    <pre className="max-h-96 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-[11px] leading-relaxed text-zinc-200">
      {children}
    </pre>
  );
}
