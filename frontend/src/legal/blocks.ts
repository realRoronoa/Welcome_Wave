/** Short ways to write a document's blocks, so the text files read as text. */
import type { LegalDocument, List, ListItem, Note, Paragraph, Section, Service, ServiceTopic, Table } from "./types";

/** A document as its own file writes it: documents.ts adds the version from VERSIONS. */
export type DocumentBody = Omit<LegalDocument, "version">;

export const p = (text: string): Paragraph => ({ type: "paragraph", text });
export const ul = (...items: ListItem[]): List => ({ type: "list", items });
export const ol = (...items: ListItem[]): List => ({ type: "list", ordered: true, items });
/** A list item that opens with its term: "Export: an admin downloads…". */
export const item = (term: string, text: string): ListItem => ({ term, text });
export const table = (columns: string[], rows: string[][], caption?: string): Table => ({ type: "table", columns, rows, ...(caption ? { caption } : {}) });
export const note = (text: string, title?: string): Note => ({ type: "note", tone: "plain", text, ...(title ? { title } : {}) });
export const caution = (text: string, title?: string): Note => ({ type: "note", tone: "caution", text, ...(title ? { title } : {}) });
export const example = (text: string, title?: string): Note => ({ type: "note", tone: "example", text, ...(title ? { title } : {}) });
/** "On this service": the page fills in how the service the reader is on is set up, for these topics (deployment.ts). */
export const service = (...topics: ServiceTopic[]): Service => ({ type: "service", topics });
export const section = (id: string, heading: string, ...blocks: Section["blocks"]): Section => ({ id, heading, blocks });
