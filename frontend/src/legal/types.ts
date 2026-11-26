/** The shape of a legal document (documents.ts holds the words; tokens.ts fills in the operator's details).
 *
 *  Text is plain: no HTML and no markdown. A string may carry two kinds of token, and nothing else is special:
 *    {{operator}} {{address}} {{contact_email}} {{privacy_email}} {{governing_law}} {{courts}}
 *        the operator's details, from GET /api/config `legal`; one that is not set reads as a neutral phrase
 *    {{doc:privacy}}
 *        a link to another document, shown as its title
 *  `segments()` in tokens.ts turns a string into the pieces a page renders, so no page parses a token itself. */

/** The six documents. The first five carry their own version (VERSIONS); `acceptable_use` is part of the terms and
 *  carries the terms' version. */
export type LegalDocId = "terms" | "privacy" | "dpa" | "subprocessors" | "recording" | "acceptable_use";

/** The documents that have a version of their own: the keys of VERSIONS here and in backend/app/legal.py. */
export type VersionedDocId = "terms" | "privacy" | "dpa" | "recording" | "subprocessors";

/** Who accepts a document, as backend/app/legal.py has it:
 *    person        each member, for themselves (terms, privacy, recording: PERSONAL)
 *    organisation  an admin, once for the organisation (dpa: ORGANISATION)
 *    none          nobody accepts it: it is published, dated and referred to (subprocessors; acceptable_use, which the
 *                  terms include) */
export type AcceptedBy = "person" | "organisation" | "none";

/** One paragraph of running text. */
export interface Paragraph { type: "paragraph"; text: string }

/** A list. An item is a sentence, or a term with its explanation. The term is a short label and the text stands on its
 *  own after it, so a page shows the label first (bold, or as a definition list's term): "Export. An admin downloads…". */
export interface List { type: "list"; ordered?: boolean; items: ListItem[] }
export type ListItem = string | { term: string; text: string };

/** A table. Every row has one cell per column; the first column is the row's heading. On a narrow screen a page may
 *  stack each row as "column: cell" pairs, so a cell must make sense next to its column's name. */
export interface Table { type: "table"; caption?: string; columns: string[]; rows: string[][] }

/** A set-apart remark. `plain` explains; `caution` is something the reader must not miss; `example` is words to copy
 *  (what a host can say before recording). */
export interface Note { type: "note"; tone: "plain" | "caution" | "example"; title?: string; text: string }

/** "On this service": the place where a page says how the service the reader is on is set up, for the statements the text
 *  leaves open ("on Google Cloud … / on a single server …", "only when the operator turns it on"). The lines come from
 *  GET /api/config `legal.deployment` (deployment.ts writes them); a copy with no server behind it (docs/legal/*.md) names
 *  what the product shows there instead. */
export interface Service { type: "service"; topics: ServiceTopic[] }
export type ServiceTopic = "hosting" | "ai" | "tracing" | "email" | "recordings" | "audit" | "signin";

export type Block = Paragraph | List | Table | Note | Service;

export interface Section {
  /** Stable within the document: the anchor (#id) and the key other documents and docs/LEGAL.md refer to. */
  id: string;
  heading: string;
  blocks: Block[];
}

export interface LegalDocument {
  id: LegalDocId;
  title: string;
  /** "YYYY-MM-DD": the date this text took effect, and the version an acceptance records. */
  version: string;
  /** One sentence: what this document is. */
  lede: string;
  /** Who it is written for, in a few words ("Everyone who signs in"). */
  audience: string;
  acceptedBy: AcceptedBy;
  /** "In brief": the honest short version, three to six sentences. It summarises; the sections are what applies. */
  summary: string[];
  /** What changed in this version, in a few plain sentences, for people who accepted the one before: the product shows it
   *  when it asks them to accept again. Leave it out of a document's first version; rewrite it with every new one. */
  changes?: string[];
  sections: Section[];
}

/** The `legal` block of GET /api/config. A detail that is not set is null (or absent). */
export interface LegalDetails {
  operator?: string | null;
  address?: string | null;
  contact_email?: string | null;
  privacy_email?: string | null;
  governing_law?: string | null;
  courts?: string | null;
  versions?: Partial<Record<VersionedDocId, string>>;
  enforced?: boolean;
  deployment?: Deployment | null;
}

/** How this server is set up, where the documents' words depend on it (backend/app/legal.py `deployment`). */
export interface Deployment {
  hosting: "google_cloud" | "single_server" | "demo";
  /** Google Cloud: the region of the database and the file store. */
  region?: string | null;
  ai: "vertex_ai" | "gemini_api" | "offline" | "none";
  /** Vertex AI: its location; "global" lets Google serve from any region. */
  ai_location?: string | null;
  email: boolean;
  tracing: boolean;
  tracing_region?: "us" | "eu" | "other" | null;
  /** A single server: days a recording's video file is kept; 0 = until someone removes it. Null on Google Cloud. */
  recording_retention_days?: number | null;
  audit_retention_days?: number | null;
  verified_email: boolean;
}

export type LegalToken = "operator" | "address" | "contact_email" | "privacy_email" | "governing_law" | "courts";

/** A piece of filled-in text. `set` is false when the operator's detail was missing and the neutral phrase stands in. */
export type Segment =
  | { kind: "text"; text: string }
  | { kind: "detail"; token: LegalToken; text: string; set: boolean }
  | { kind: "email"; token: LegalToken; address: string }
  | { kind: "doc"; id: LegalDocId; title: string };
