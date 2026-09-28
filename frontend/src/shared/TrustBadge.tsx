import type { ReactElement } from "react";

/**
 * Trust state is the product's whole thesis, so it gets one component and one
 * vocabulary. Colour alone would fail in greyscale and for colour-blind
 * readers, so each state also has its own typographic form:
 *
 *   verified  mono, uppercase, tinted, tilted  — a stamp
 *   stale     the same stamp, gone cold        — stamped, then undermined
 *   draft     serif italic, dashed, no fill    — provisional by shape
 *
 * These used to be built as HTML strings with the icon functions interpolated
 * into a template literal, which stringified the React elements to
 * "[object Object]" in front of every label.
 */

export type TrustState = "verified" | "draft" | "stale";

const ICONS: Record<TrustState, ReactElement> = {
  verified: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 10.5l4 4 8-9" />
    </svg>
  ),
  draft: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 15l1-4 8-8 3 3-8 8-4 1z" />
    </svg>
  ),
  stale: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="10" cy="10" r="7" />
      <path d="M10 6v4.2l2.8 1.7" />
    </svg>
  ),
};

const CLASS: Record<TrustState, string> = {
  verified: "badge badge-verified",
  draft: "badge badge-draft",
  stale: "badge badge-stale",
};

export function trustStateOf(item: { status: "verified" | "draft"; stale?: boolean }): TrustState {
  if (item.stale) return "stale";
  return item.status;
}

type Props = {
  state: TrustState;
  /** Who signed off. Shown only for a standing verified answer. */
  verifiedBy?: string;
};

export default function TrustBadge({ state, verifiedBy }: Props) {
  const text =
    state === "verified"
      ? verifiedBy
        ? `Verified · ${verifiedBy}`
        : "Verified"
      : state === "stale"
        ? "Needs re-verification"
        : "AI draft · unverified";

  return (
    <span className={CLASS[state]}>
      {ICONS[state]}
      {text}
    </span>
  );
}
