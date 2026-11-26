#!/usr/bin/env node
// Writes docs/legal/*.md from the legal documents' one source, frontend/src/legal/documents.ts.
//
//   node docs/legal/build.mjs                 write TERMS.md, PRIVACY.md, RECORDING.md, ACCEPTABLE_USE.md, DPA.md,
//                                             SUBPROCESSORS.md and README.md here
//   node docs/legal/build.mjs --check         write nothing; exit 1 if a file here differs from the source (run before a release)
//   node docs/legal/build.mjs --filled --out <folder>
//                                             a copy with the operator's details filled in from the environment
//                                             (LEGAL_OPERATOR_NAME, LEGAL_OPERATOR_ADDRESS, LEGAL_CONTACT_EMAIL,
//                                             LEGAL_PRIVACY_EMAIL, LEGAL_GOVERNING_LAW, LEGAL_COURTS), to send to a
//                                             lawyer or a customer. Never into this folder: the checked-in copies
//                                             name no company.
//
// No new dependency: the TypeScript is bundled with the esbuild that Vite already installs (frontend/node_modules;
// run `npm ci` in frontend/ first). The script also checks the documents: every token is known, section ids are unique
// within a document, table rows fit their columns, versions are dates.
import { mkdirSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const SOURCE = join(ROOT, "frontend/src/legal/documents.ts");
const FILES = { terms: "TERMS.md", privacy: "PRIVACY.md", recording: "RECORDING.md", acceptable_use: "ACCEPTABLE_USE.md", dpa: "DPA.md",
                subprocessors: "SUBPROCESSORS.md" };
const SETTINGS = { operator: "LEGAL_OPERATOR_NAME", address: "LEGAL_OPERATOR_ADDRESS", contact_email: "LEGAL_CONTACT_EMAIL",
                   privacy_email: "LEGAL_PRIVACY_EMAIL", governing_law: "LEGAL_GOVERNING_LAW", courts: "LEGAL_COURTS" };
const ACCEPTED = { person: "Each person, for themselves, in the product.", organisation: "An admin, once for the organisation, in the product.",
                   none: "Nobody accepts it on its own: it is published, dated and referred to by the other documents." };

const args = process.argv.slice(2);
const check = args.includes("--check");
const filled = args.includes("--filled");
const outAt = args.indexOf("--out");
const OUT = outAt >= 0 ? resolve(args[outAt + 1] ?? "") : HERE;
if (filled && OUT === HERE) fail("--filled needs --out <another folder>: the checked-in copies name no company.");
if (filled && check) fail("--check compares the checked-in copies, which are never filled in.");

function fail(message) { console.error(`docs/legal/build.mjs: ${message}`); process.exit(1); }

async function load() {
  let esbuild;
  try { esbuild = await import(pathToFileURL(join(ROOT, "frontend/node_modules/esbuild/lib/main.js")).href); }
  catch { fail("esbuild is not installed. Run `npm ci` in frontend/ first (Vite brings it)."); }
  const built = await (esbuild.build ?? esbuild.default.build)({ entryPoints: [SOURCE], bundle: true, format: "esm", platform: "node", write: false,
                                                                 logLevel: "silent" });
  const file = join(tmpdir(), `ww-legal-${process.pid}.mjs`);
  writeFileSync(file, built.outputFiles[0].text);
  try { return await import(pathToFileURL(file).href); } finally { rmSync(file, { force: true }); }
}

const L = await load();
const legal = filled ? Object.fromEntries(Object.entries(SETTINGS).map(([key, env]) => [key, process.env[env] ?? null])) : {};
const used = new Set(); // the tokens the document being written uses

// ---------------------------------------------------------------- text
const esc = (s) => s.replace(/([\\`*_<>|])/g, "\\$1").replace(/\[/g, "\\[");
function inline(text) {
  return L.parts(text, legal).map((s) => {
    if (s.kind === "text") {
      if (/\{\{|\}\}/.test(s.text)) problems.push(`unknown token in: ${s.text.slice(0, 80)}`);
      return esc(s.text);
    }
    if (s.kind === "doc") return `[${esc(s.title)}](${FILES[s.id]})`;
    used.add(s.token);
    return s.kind === "email" ? `<${s.address}>` : esc(s.text);
  }).join("");
}
const cell = (text) => inline(text).replace(/\n/g, " ");

function block(b) {
  if (b.type === "paragraph") return inline(b.text);
  if (b.type === "list") {
    return b.items.map((it, i) => {
      const mark = b.ordered ? `${i + 1}.` : "-";
      return typeof it === "string" ? `${mark} ${inline(it)}` : `${mark} **${inline(it.term)}.** ${inline(it.text)}`;
    }).join("\n");
  }
  if (b.type === "table") {
    const head = `| ${b.columns.map(cell).join(" | ")} |\n|${b.columns.map(() => "---").join("|")}|`;
    const rows = b.rows.map((r) => `| ${r.map(cell).join(" | ")} |`).join("\n");
    return `${b.caption ? `${inline(b.caption)}\n\n` : ""}${head}\n${rows}`;
  }
  if (b.type === "note") return `> ${b.title ? `**${inline(b.title)}.** ` : ""}${inline(b.text)}`;
  // a copy has no server behind it: it names what the product shows in this place (frontend/src/legal/deployment.ts)
  if (b.type === "service") {
    for (const t of b.topics) if (!L.SERVICE_TOPICS[t]) problems.push(`unknown "On this service" topic: ${t}`);
    return `> **On this service.** ${esc(L.serviceNote(b.topics))}`;
  }
  problems.push(`unknown block type: ${b.type}`);
  return "";
}

// ---------------------------------------------------------------- checks
const problems = [];
function validate(doc) {
  const at = (what) => problems.push(`${doc.id}: ${what}`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(doc.version)) at(`version ${doc.version} is not a date`);
  if (doc.summary.length < 3 || doc.summary.length > 6) at(`"In brief" has ${doc.summary.length} lines (3 to 6)`);
  const seen = new Set();
  for (const s of doc.sections) {
    if (!/^[a-z0-9-]+$/.test(s.id)) at(`section id "${s.id}" is not lower-case words joined by hyphens`);
    if (seen.has(s.id)) at(`section id "${s.id}" is used twice`);
    // ids the site's own page uses (main#content, #top, the "In brief" box): a section with one could not be linked to
    if (["content", "top", "brief", "lg-h", "s-menu"].includes(s.id)) at(`section id "${s.id}" is taken by the page itself`);
    seen.add(s.id);
    if (!s.blocks.length) at(`section "${s.id}" is empty`);
    for (const b of s.blocks) {
      if (b.type === "table") for (const r of b.rows) if (r.length !== b.columns.length) at(`a row in "${s.id}" has ${r.length} cells for ${b.columns.length} columns`);
    }
  }
}

// ---------------------------------------------------------------- one document
const HEADER = "<!-- Generated by docs/legal/build.mjs from frontend/src/legal/. Do not edit: change the source, then run `node docs/legal/build.mjs`. -->";
const status = () => (L.LEGAL_REVIEW.status === "reviewed" ? "" : ` · **${L.LEGAL_REVIEW.note}**`);

function page(doc) {
  used.clear();
  validate(doc);
  const body = [
    `# ${doc.title}`,
    `Version ${doc.version}${status()}`,
    `${inline(doc.lede)}`,
    `**For:** ${inline(doc.audience)}\\\n**Accepted by:** ${ACCEPTED[doc.acceptedBy]}`,
    `## In brief`,
    doc.summary.map((s) => `- ${inline(s)}`).join("\n"),
    ...doc.sections.flatMap((s) => [`## ${inline(s.heading)}`, ...s.blocks.map(block)]),
  ];
  const tokens = L.TOKENS.filter((t) => used.has(t));
  const settings = !tokens.length || filled ? [] : [
    "---",
    "The operator's details are not written into this text. The product fills them in from the server's settings; this copy "
    + "shows what a reader sees while one is not set:",
    tokens.map((t) => `- \`${SETTINGS[t]}\`: “${L.FALLBACKS[t]}”`).join("\n"),
  ];
  return `${[HEADER, ...body, ...settings].join("\n\n")}\n`;
}

function index() {
  const rows = L.DOCUMENT_LIST.map((d) => `| [${d.title}](${FILES[d.id]}) | ${d.version} | ${esc(d.audience)} | ${ACCEPTED[d.acceptedBy]} |`);
  return `${[
    HEADER,
    "# Legal documents",
    "The text of each document the product shows, for reading and review. The source is `frontend/src/legal/`; "
    + "[LEGAL.md](../LEGAL.md) says where each one appears, what to decide with a lawyer, and how to publish a new version. "
    + "[DATA_INVENTORY.md](DATA_INVENTORY.md) is the evidence behind every statement about data.",
    ...(L.LEGAL_REVIEW.status === "reviewed" ? [] : [`**${L.LEGAL_REVIEW.note}**`]),
    `| Document | Version | For | Accepted by |\n|---|---|---|---|\n${rows.join("\n")}`,
    "Regenerate after changing the source: `node docs/legal/build.mjs`. Check that these files match it: `node docs/legal/build.mjs --check`.",
  ].join("\n\n")}\n`;
}

// ---------------------------------------------------------------- write or compare
const out = Object.fromEntries(L.DOCUMENT_LIST.map((d) => [FILES[d.id], page(d)]));
out["README.md"] = index();
for (const id of Object.keys(L.DOCUMENTS)) if (!FILES[id]) problems.push(`no file name for document "${id}"`);
// the site's page for each document (frontend/src/legal/paths.ts) names it as the document names itself
for (const id of Object.keys(L.DOCUMENTS)) {
  const pg = (L.LEGAL_PAGES ?? []).find((x) => x.id === id);
  if (!pg) problems.push(`no page in frontend/src/legal/paths.ts for document "${id}"`);
  else if (pg.title !== L.DOCUMENTS[id].title) problems.push(`paths.ts calls "${id}" "${pg.title}"; the document's title is "${L.DOCUMENTS[id].title}"`);
}
if (problems.length) fail(`the documents have problems:\n  - ${[...new Set(problems)].join("\n  - ")}`);

if (check) {
  const stale = Object.entries(out).filter(([name, text]) => !existsSync(join(OUT, name)) || readFileSync(join(OUT, name), "utf8") !== text).map(([name]) => name);
  if (stale.length) fail(`out of date: ${stale.join(", ")}. Run \`node docs/legal/build.mjs\`.`);
  console.log(`docs/legal: ${Object.keys(out).length} files match frontend/src/legal.`);
} else {
  mkdirSync(OUT, { recursive: true });
  for (const [name, text] of Object.entries(out)) writeFileSync(join(OUT, name), text);
  console.log(`Wrote ${Object.keys(out).length} files to ${OUT}${filled ? " (operator's details filled in from the environment)" : ""}.`);
}
