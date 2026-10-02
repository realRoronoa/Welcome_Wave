import type { ReactElement } from "react";
import type { FunctionalRole, ViewName } from "../types";

/**
 * Every icon is a 20×20 line glyph on `currentColor`, so it inherits whatever
 * colour its context sets and stays legible at small sizes.
 *
 * `width`/`height` are set here as real attributes, not left to CSS. An SVG
 * with only a `viewBox` has no intrinsic size and expands to fill its parent,
 * so without these a stylesheet that fails to load turns every icon into a
 * full-width illustration. CSS can still resize them; this is the floor.
 */
export const glyph = {
  width: 20,
  height: 20,
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

export const VIEW_ICON: Record<ViewName, ReactElement> = {
  roadmap: (
    <svg viewBox="0 0 20 20" strokeWidth="1.6" aria-hidden="true" {...glyph}>
      <path d="M4 16V6l4-2 4 2 4-2v10l-4 2-4-2-4 2z" />
      <path d="M8 4v10M12 6v10" />
    </svg>
  ),
  ask: (
    <svg viewBox="0 0 20 20" strokeWidth="1.6" aria-hidden="true" {...glyph}>
      <path d="M4 4h12v9H8l-4 3z" />
    </svg>
  ),
  verifyQueue: (
    <svg viewBox="0 0 20 20" strokeWidth="1.8" aria-hidden="true" {...glyph}>
      <path d="M4 10.5l4 4 8-9" />
    </svg>
  ),
  dashboard: (
    <svg viewBox="0 0 20 20" strokeWidth="1.7" aria-hidden="true" {...glyph}>
      <path d="M4 15V9M10 15V5m6 10v-6" />
    </svg>
  ),
};

export const DocumentIcon = () => (
  <svg viewBox="0 0 20 20" strokeWidth="1.7" aria-hidden="true" {...glyph}>
    <path d="M6 3h6l3 3v11H6z" />
    <path d="M12 3v3h3" />
  </svg>
);

export const ArrowIcon = () => (
  <svg viewBox="0 0 20 20" strokeWidth="1.9" aria-hidden="true" {...glyph}>
    <path d="M4 10h11M11 6l4 4-4 4" />
  </svg>
);

export const CheckIcon = () => (
  <svg viewBox="0 0 20 20" strokeWidth="2.2" aria-hidden="true" {...glyph}>
    <path d="M4 10.5l4 4 8-9" />
  </svg>
);

export const MenuIcon = () => (
  <svg viewBox="0 0 20 20" strokeWidth="1.7" aria-hidden="true" {...glyph}>
    <path d="M3 5h14M3 10h14M3 15h14" />
  </svg>
);

export const SearchIcon = () => (
  <svg viewBox="0 0 20 20" strokeWidth="1.7" aria-hidden="true" {...glyph}>
    <circle cx="9" cy="9" r="5.5" />
    <path d="M13 13l4 4" />
  </svg>
);

export const ROLE_ICON: Record<FunctionalRole, ReactElement> = {
  new_hire: (
    <svg viewBox="0 0 20 20" strokeWidth="1.6" aria-hidden="true" {...glyph}>
      <path d="M10 3l1.9 4.6 4.6 1.9-4.6 1.9L10 16l-1.9-4.6L3.5 9.5l4.6-1.9z" />
    </svg>
  ),
  domain_expert: (
    <svg viewBox="0 0 20 20" strokeWidth="1.6" aria-hidden="true" {...glyph}>
      <path d="M10 3l6 2.5v4c0 4-2.5 6.8-6 7.5-3.5-.7-6-3.5-6-7.5v-4L10 3z" />
      <path d="M7.5 10l1.8 1.8L13 8" />
    </svg>
  ),
  manager: (
    <svg viewBox="0 0 20 20" strokeWidth="1.7" aria-hidden="true" {...glyph}>
      <path d="M4 15V9M10 15V5m6 10v-6" />
    </svg>
  ),
  admin: (
    <svg viewBox="0 0 20 20" strokeWidth="1.6" aria-hidden="true" {...glyph}>
      <rect x="3" y="4" width="14" height="12" rx="1.5" />
      <path d="M3 8h14" />
    </svg>
  ),
};
