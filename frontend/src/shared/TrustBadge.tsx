import type { ReactElement } from "react";
import type { AnswerStatus } from "../types";
import { glyph } from "./icons";

/**
 * The one component Ask, Verify queue, and Dashboard all use to say how much
 * an answer can be trusted — which is why it lives in `shared/` rather than in
 * any single feature folder.
 *
 * Trust is the product's whole thesis, so each state gets its own typographic
 * form as well as its own colour. Colour alone would disappear in greyscale
 * and for colour-blind readers:
 *
 *   verified   mono, uppercase, tinted, tilted   — a stamp
 *   stale      the same stamp, gone cold         — stamped, then undermined
 *   ai_draft   serif italic, dashed, no fill     — provisional by shape
 *   not_found  quiet, dotted, no claim at all
 */

const ICON: Record<AnswerStatus, ReactElement> = {
  verified: (
    <svg viewBox="0 0 20 20" strokeWidth="2.4" aria-hidden="true" {...glyph}>
      <path d="M4 10.5l4 4 8-9" />
    </svg>
  ),
  ai_draft: (
    <svg viewBox="0 0 20 20" strokeWidth="1.7" aria-hidden="true" {...glyph}>
      <path d="M5 15l1-4 8-8 3 3-8 8-4 1z" />
    </svg>
  ),
  stale: (
    <svg viewBox="0 0 20 20" strokeWidth="1.8" aria-hidden="true" {...glyph}>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 6v4.2l2.8 1.7" />
    </svg>
  ),
  not_found: (
    <svg viewBox="0 0 20 20" strokeWidth="1.7" aria-hidden="true" {...glyph}>
      <circle cx="10" cy="10" r="7" />
      <path d="M7.4 7.4l5.2 5.2" />
    </svg>
  ),
};

const CLASS: Record<AnswerStatus, string> = {
  verified: "badge badge-verified",
  ai_draft: "badge badge-draft",
  stale: "badge badge-stale",
  not_found: "badge badge-missing",
};

const LABEL: Record<AnswerStatus, string> = {
  verified: "Verified",
  ai_draft: "AI draft — unverified",
  stale: "Needs re-verification",
  not_found: "No grounded answer",
};

interface Props {
  status: AnswerStatus;
  /** Who signed off. Appended only to a standing verified answer. */
  verifiedBy?: string;
}

export default function TrustBadge({ status, verifiedBy }: Props) {
  const text =
    status === "verified" && verifiedBy ? `${LABEL.verified} · ${verifiedBy}` : LABEL[status];

  return (
    <span className={CLASS[status]}>
      {ICON[status]}
      {text}
    </span>
  );
}
