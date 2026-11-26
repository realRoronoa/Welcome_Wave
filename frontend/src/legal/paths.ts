/** Where each legal document is published on the site, and its names. No document text here: the site's shell, the
 *  footer, the sign-in screens and the app import this without downloading the documents themselves.
 *
 *  Three other places list the same six paths, and all four must agree (see the comment in src/surface.ts):
 *    src/surface.ts               every path here is the site's (it is any path that is not the app's)
 *    public/boot-theme.js         LEGAL: these pages open without the intro
 *    nginx.conf.template          the shell is served for them with 200; any other unknown path gets 404
 *  `title` repeats each document's own title (documents.ts): docs/legal/build.mjs --check fails when they differ. */
import type { LegalDocId } from "./types";

export type LegalPage = { path: string; id: LegalDocId; title: string; short: string };

/** In reading order: what a person accepts first, then what an admin needs (documents.ts DOCUMENT_ORDER). */
export const LEGAL_PAGES: readonly LegalPage[] = [
  { path: "/terms", id: "terms", title: "Terms of Service", short: "Terms" },
  { path: "/privacy", id: "privacy", title: "Privacy Policy", short: "Privacy" },
  { path: "/recording", id: "recording", title: "Recording and AI Notice", short: "Recording notice" },
  { path: "/acceptable-use", id: "acceptable_use", title: "Acceptable Use", short: "Acceptable use" },
  { path: "/dpa", id: "dpa", title: "Data Processing Agreement", short: "DPA" },
  { path: "/subprocessors", id: "subprocessors", title: "Sub-processors", short: "Sub-processors" },
];

export const LEGAL_PATH: Record<LegalDocId, string> = Object.fromEntries(LEGAL_PAGES.map((p) => [p.id, p.path])) as Record<LegalDocId, string>;

/** The page at a site path ("/terms"), or null. */
export function legalPage(path: string): LegalPage | null {
  return LEGAL_PAGES.find((p) => p.path === path) ?? null;
}

/** "2026-11-19" as "19 November 2026": the same words whatever the reader's locale, since the documents are in English. */
export function versionDate(version: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(version);
  if (!m) return version;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}
