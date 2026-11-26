/** Filling a legal text's tokens with the operator's details (GET /api/config `legal`). No React, no DOM: the web pages
 *  and the script that writes docs/legal/*.md (docs/legal/build.mjs) use the same code, so both say the same thing.
 *
 *  A detail that is not set is never invented and never shown as a raw placeholder: the neutral phrase in FALLBACKS
 *  stands in, and the segment says `set: false` so a page can, if it wants, tell an admin which setting is missing. */
import type { LegalDetails, LegalDocId, LegalToken, Segment } from "./types";

/** What a missing detail reads as. Each fits where the documents use its token:
 *    "Welcome Wave is run by {{operator}}"         "Postal address: {{address}}"
 *    "write to {{contact_email}}"                  "write to {{privacy_email}}"
 *    "governed by {{governing_law}}"               "go to {{courts}}"
 *  LEGAL_GOVERNING_LAW and LEGAL_COURTS are whole phrases ("the laws of India", "the courts of Chennai, India"), as
 *  backend/app/config.py asks for them. */
export const FALLBACKS: Record<LegalToken, string> = {
  operator: "the company that operates this service",
  address: "the operator's registered office",
  contact_email: "the support contact for this service",
  privacy_email: "the privacy contact for this service",
  governing_law: "the laws of the place where the operator is established",
  courts: "the courts of that place",
};

export const TOKENS: readonly LegalToken[] = ["operator", "address", "contact_email", "privacy_email", "governing_law", "courts"];

const EMAILS: ReadonlySet<LegalToken> = new Set<LegalToken>(["contact_email", "privacy_email"]);
const TOKEN = /\{\{\s*(doc:)?([a-z_]+)\s*\}\}/g;
const SENTENCE_END = /(^|[.!?:]\s+)$/;

const clean = (v: string | null | undefined) => (typeof v === "string" && v.trim() ? v.trim() : null);

/** The value a token stands for, or null when it is not set. A missing privacy address falls back to the contact
 *  address (as backend/app/legal.py says it does) before the neutral phrase. */
export function detail(legal: LegalDetails | null | undefined, token: LegalToken): string | null {
  const own = clean(legal?.[token]);
  if (own || token !== "privacy_email") return own;
  return clean(legal?.contact_email);
}

/** The settings that are not filled in, in a fixed order: for an admin's "still to set" hint. */
export function missingDetails(legal: LegalDetails | null | undefined): LegalToken[] {
  return TOKENS.filter((t) => detail(legal, t) === null);
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** One string as the pieces to render. `titles` names the other documents (documents.ts passes its own). A token that
 *  is not known is left as text, so a typo shows up in review instead of vanishing. A neutral phrase that opens a
 *  sentence gets its capital letter. */
export function segments(text: string, legal: LegalDetails | null | undefined, titles: Partial<Record<LegalDocId, string>> = {}): Segment[] {
  const out: Segment[] = [];
  const push = (t: string) => {
    if (!t) return;
    const last = out[out.length - 1];
    if (last && last.kind === "text") last.text += t;
    else out.push({ kind: "text", text: t });
  };
  let at = 0;
  for (const m of text.matchAll(TOKEN)) {
    const before = text.slice(at, m.index);
    push(before);
    at = (m.index ?? 0) + m[0].length;
    const name = m[2];
    if (m[1]) {
      const title = titles[name as LegalDocId];
      if (title) out.push({ kind: "doc", id: name as LegalDocId, title });
      else push(m[0]);
      continue;
    }
    if (!(TOKENS as readonly string[]).includes(name)) { push(m[0]); continue; }
    const token = name as LegalToken;
    const value = detail(legal, token);
    if (value !== null && EMAILS.has(token)) { out.push({ kind: "email", token, address: value }); continue; }
    if (value !== null) { out.push({ kind: "detail", token, text: value, set: true }); continue; }
    const opens = SENTENCE_END.test(text.slice(0, m.index));
    out.push({ kind: "detail", token, text: opens ? capital(FALLBACKS[token]) : FALLBACKS[token], set: false });
  }
  push(text.slice(at));
  return out;
}

/** The same, as one plain string (document links read as the document's title). */
export function fill(text: string, legal: LegalDetails | null | undefined, titles: Partial<Record<LegalDocId, string>> = {}): string {
  return segments(text, legal, titles).map((s) => (s.kind === "email" ? s.address : s.kind === "doc" ? s.title : s.text)).join("");
}
