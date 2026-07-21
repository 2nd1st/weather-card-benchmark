"use client";

import type { ReactNode } from "react";

// FilterBar + FacetChip (spec §5, §4.9 gallery). Compositional shell — the
// gallery (B2) owns the actual facet logic + URL round-trip; these are the
// styled, accessible building blocks. Multi-select chips (OR within a facet,
// AND across facets); toggles for live / valid-only / has-patch. Violet chips
// are for community/vote facets only (§5 colour reservation).

/** The toolbar container. */
export function FilterBar({
  children,
  ariaLabel,
}: {
  children: ReactNode;
  ariaLabel?: string;
}) {
  return (
    <div className="toolbar" role="group" aria-label={ariaLabel}>
      {children}
    </div>
  );
}

/** A labelled group of facet chips. */
export function FacetGroup({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="fgroup">
      <span className="gl">{label}</span>
      {/* chips live in their own wrapping row — grid hosts (gallery's 88px/1fr
          header+chips layout) must not slot each chip into alternating columns. */}
      <div className="fchips">{children}</div>
    </div>
  );
}

/** A single multi-select facet chip. `violet` marks community/vote facets. */
export function FacetChip({
  label,
  active,
  onToggle,
  violet,
  disabled,
  title,
}: {
  label: ReactNode;
  active?: boolean;
  onToggle?: () => void;
  violet?: boolean;
  disabled?: boolean;
  title?: string;
}) {
  const cls = ["chip", active ? (violet ? "on2" : "on") : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      type="button"
      className={cls}
      aria-pressed={active}
      onClick={onToggle}
      disabled={disabled}
      title={title}
    >
      {label}
    </button>
  );
}

/** A boolean pill switch (live / valid-only / has-patch). */
export function FilterToggle({
  label,
  on,
  onToggle,
}: {
  label: ReactNode;
  on?: boolean;
  onToggle?: () => void;
}) {
  return (
    <button
      type="button"
      className={on ? "switch on" : "switch"}
      aria-pressed={on}
      onClick={onToggle}
    >
      <span className="sw" aria-hidden />
      {label}
    </button>
  );
}

/** Model-id search box. */
export function SearchInput({
  value,
  onChange,
  placeholder,
  ariaLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  ariaLabel?: string;
}) {
  return (
    <label className="search">
      <span aria-hidden>⌕</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel ?? placeholder}
      />
    </label>
  );
}

/** Right-aligned cluster for toggles / density controls. */
export function ToolsSide({ children }: { children: ReactNode }) {
  return <div className="tools-side">{children}</div>;
}
