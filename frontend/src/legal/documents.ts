/** The legal documents, as structured data: the one source of their words.
 *
 *  The web pages render these; `node docs/legal/build.mjs` writes the same words to docs/legal/*.md. Each is a careful
 *  first draft for the founders' lawyer, not legal advice (LEGAL_REVIEW below; docs/LEGAL.md says what to decide).
 *
 *  Changing a document's words means a new version: change its date in VERSIONS here AND in backend/app/legal.py
 *  (tests/test_legal.py fails when the two differ). Everyone is then asked to accept it again, once.
 *
 *  Every statement about what the product does with data is backed by docs/legal/DATA_INVENTORY.md. Change the code,
 *  and the inventory and these words change with it. */
import { acceptableUse } from "./acceptable_use";
import type { DocumentBody } from "./blocks";
import { dpa } from "./dpa";
import { privacy } from "./privacy";
import { recording } from "./recording";
import { subprocessors } from "./subprocessors";
import { terms } from "./terms";
import { fill, segments } from "./tokens";
import type { LegalDetails, LegalDocId, LegalDocument, Segment, VersionedDocId } from "./types";

export const VERSIONS = {
  terms: "2026-11-19",
  privacy: "2026-11-19",
  dpa: "2026-11-19",
  recording: "2026-11-19",
  subprocessors: "2026-11-19",
} as const;

/** Whether a lawyer has reviewed the published words. `draft` until the founders' lawyer has signed them off: the
 *  generated docs/legal/*.md say so at the top, and a page may show a quiet "Draft" mark. Set it to "reviewed" then. */
export const LEGAL_REVIEW: { status: "draft" | "reviewed"; note: string } = {
  status: "draft",
  note: "A first draft, not yet reviewed by a lawyer.",
};

/** Acceptable use is part of the terms: it is accepted with them and carries their version. */
const VERSION_OF: Record<LegalDocId, VersionedDocId> = {
  terms: "terms", privacy: "privacy", dpa: "dpa", recording: "recording", subprocessors: "subprocessors", acceptable_use: "terms",
};

const versioned = (body: DocumentBody): LegalDocument => ({ ...body, version: VERSIONS[VERSION_OF[body.id]] });

/** Every document, by id. */
export const DOCUMENTS: Record<LegalDocId, LegalDocument> = {
  terms: versioned(terms),
  privacy: versioned(privacy),
  recording: versioned(recording),
  dpa: versioned(dpa),
  subprocessors: versioned(subprocessors),
  acceptable_use: versioned(acceptableUse),
};

/** The order to list them in: what a person accepts first, then what an admin needs. */
export const DOCUMENT_ORDER: readonly LegalDocId[] = ["terms", "privacy", "recording", "acceptable_use", "dpa", "subprocessors"];
export const DOCUMENT_LIST: readonly LegalDocument[] = DOCUMENT_ORDER.map((id) => DOCUMENTS[id]);

export const DOCUMENT_TITLES: Record<LegalDocId, string> = {
  terms: terms.title, privacy: privacy.title, recording: recording.title, acceptable_use: acceptableUse.title, dpa: dpa.title,
  subprocessors: subprocessors.title,
};

/** The document for an id from a URL or the API ("acceptable-use" is read as "acceptable_use"), or null. */
export function getDocument(id: string | null | undefined): LegalDocument | null {
  const key = (id ?? "").trim().toLowerCase().replace(/-/g, "_");
  return Object.prototype.hasOwnProperty.call(DOCUMENTS, key) ? DOCUMENTS[key as LegalDocId] : null;
}

/** A text of a document as the pieces to render, with the operator's details filled in (tokens.ts `segments`). */
export const parts = (text: string, legal: LegalDetails | null | undefined): Segment[] => segments(text, legal, DOCUMENT_TITLES);

/** The same as one plain string: for a title attribute, a search index, a plain-text copy. */
export const plain = (text: string, legal: LegalDetails | null | undefined): string => fill(text, legal, DOCUMENT_TITLES);

export { FALLBACKS, TOKENS, detail, fill, missingDetails, segments } from "./tokens";
/** "On this service": how the service a reader is on is set up, where the words depend on it (deployment.ts). */
export { SERVICE_TOPICS, serviceLines, serviceNote } from "./deployment";
/** Where each document is published on the site (paths.ts holds no text, so the site's shell imports it on its own). */
export { LEGAL_PAGES, LEGAL_PATH, legalPage, versionDate } from "./paths";
export type { LegalPage } from "./paths";
export type {
  AcceptedBy, Block, Deployment, LegalDetails, LegalDocId, LegalDocument, LegalToken, List, ListItem, Note, Paragraph, Section, Segment,
  Service, ServiceTopic, Table, VersionedDocId,
} from "./types";
