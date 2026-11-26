/** Building blocks shared by the settings sections, the new-project page and the GitHub callback. */
import { ReactNode } from "react";
import { ROLE_LABEL } from "../../api";
import { initials } from "../../auth/Entry";
import "../../styles/entry.css";

/** A settings section: a header (title, one line of description, optional actions) and a body. */
export function SectionCard({ id, title, description, actions, tone, children }:
  { id: string; title: ReactNode; description?: ReactNode; actions?: ReactNode; tone?: "danger"; children?: ReactNode }) {
  return (
    <section className={`set-card ${tone === "danger" ? "danger" : ""}`} aria-labelledby={id}>
      <header className="set-card-head">
        <div className="set-card-title">
          <h2 id={id}>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="set-card-actions">{actions}</div>}
      </header>
      {children !== undefined && <div className="set-card-body">{children}</div>}
    </section>
  );
}

/** A round avatar with initials. `you` gives it the teal ring; `pending` a dashed outline. */
export function Avatar({ name, you, pending, size = 32 }: { name: string; you?: boolean; pending?: boolean; size?: number }) {
  return (
    <span className={`set-avatar ${you ? "you" : ""} ${pending ? "pending" : ""}`} style={{ width: size, height: size, fontSize: size * .36 }} aria-hidden="true">
      {initials(name)}
    </span>
  );
}

export function RolePill({ role }: { role: string }) {
  return <span className={`set-role r-${role}`}>{ROLE_LABEL[role] ?? role}</span>;
}

/** The GitHub mark (currentColor). */
export function GitHubMark({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

/** Small stroke glyphs used only in these pages (24px grid, Lucide-style). */
const GLYPHS = {
  branch: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9",
  commit: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 12h6M15 12h6",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6",
  lock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  check: "M20 6L9 17l-5-5",
  x: "M18 6L6 18M6 6l12 12",
  link: "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71",
  user: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  mail: "M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 6l-10 7L2 6",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35",
  calendar: "M3 6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM16 2v4M8 2v4M3 10h18",
  alert: "M12 9v4M12 17h.01M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z",
  folder: "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z",
  sparkle: "M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z",
};
export type GlyphName = keyof typeof GLYPHS;
export function Glyph({ name, size = 16, className }: { name: GlyphName; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={GLYPHS[name]} /></svg>
  );
}

/** A file-type tile: a folded page with the extension on it, tinted by type. */
export function FileTile({ ext }: { ext: string }) {
  const kind = ext === "pdf" ? "pdf" : ext === "docx" || ext === "doc" ? "doc" : ext === "md" || ext === "markdown" ? "md" : "txt";
  return (
    <span className={`set-file k-${kind}`} aria-hidden="true">
      <svg viewBox="0 0 32 40" width="32" height="40">
        <path d="M4 1h17l10 10v25a3 3 0 0 1-3 3H4a3 3 0 0 1-3-3V4a3 3 0 0 1 3-3z" className="set-file-page" />
        <path d="M21 1v7a3 3 0 0 0 3 3h7" className="set-file-fold" />
      </svg>
      <span className="set-file-ext">{kind === "doc" ? "DOC" : kind.toUpperCase()}</span>
    </span>
  );
}

/** The extension of a document, from its MIME type, file name or storage path. */
export function extOf(doc: { title: string; mime?: string | null; uri?: string | null }) {
  const m = doc.mime ?? "";
  if (m.includes("pdf")) return "pdf";
  if (m.includes("wordprocessingml") || m.includes("msword")) return "docx";
  if (m.includes("markdown")) return "md";
  const name = doc.uri || doc.title;
  const e = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  return e || (m.startsWith("text/") ? "txt" : "");
}

/** Whole days from a date (YYYY-MM-DD) to today: 0 is today, negative is in the future. */
export function daysSince(date: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!m) return null;
  const start = new Date(+m[1], +m[2] - 1, +m[3]), now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((today.getTime() - start.getTime()) / 864e5);
}
