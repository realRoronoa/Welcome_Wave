// WCAG contrast check for the "Ink + Teal" tokens (docs/brand/BRAND.md). Reads the tokens from frontend/src/styles.css, so
// it checks what ships: the light :root block, the dark block (layered over light), the rail block, and the panel scope
// (the navy surface of forms, dialogs, popovers and sheets, which re-points --surface, --ink-soft and friends).
//
//   node docs/brand/contrast.mjs          prints every pair; exits 1 if any falls below its minimum
//
// Minimums: 4.5:1 for text (WCAG 1.4.3 AA), 3:1 for the edges and marks that identify a control or a state (1.4.11).
// A colour with alpha is composited over the background it sits on (and that over the page), as the browser does.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../../frontend/src/styles.css"), "utf8");

function block(selector) {
  const at = css.indexOf(selector + " {");
  if (at < 0) throw new Error(`no block ${selector}`);
  let depth = 0, i = css.indexOf("{", at);
  const start = i + 1;
  for (; i < css.length; i++) { if (css[i] === "{") depth++; else if (css[i] === "}" && --depth === 0) break; }
  const out = {};
  for (const m of css.slice(start, i).replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const light = block(":root");
const dark = { ...light, ...block(':root[data-theme="dark"]') };
const rail = block(":is(.rail, .topbar)");
const panel = block("html :is(.ww-panel, .sc-dialog, .dp-dialog, .pal, .fb-card, .demo-clock-pop, .notif-panel,\n          .lc-enrol, .lc-create, .lc-estimate, .np-card, .set-invite, .set-inset, .splash .card, .consent, .en-side)");

function parse(v) {
  v = v.trim();
  let m = /^#([0-9a-f]{6})$/i.exec(v);
  if (m) return [0, 2, 4].map((k) => parseInt(m[1].slice(k, k + 2), 16)).concat(1);
  m = /^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+))?\s*\)$/.exec(v);
  if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
  return null;
}

function resolve(tokens, name, seen = new Set()) {
  if (seen.has(name)) throw new Error(`cycle at ${name}`);
  seen.add(name);
  const raw = tokens[name];
  if (raw === undefined) throw new Error(`unknown token ${name}`);
  const ref = /^var\((--[\w-]+)(?:,\s*([^)]+))?\)$/.exec(raw);
  if (ref) return tokens[ref[1]] !== undefined ? resolve(tokens, ref[1], seen) : parse(ref[2]);
  const c = parse(raw);
  if (!c) throw new Error(`${name}: cannot read ${raw}`);
  return c;
}

const over = (fg, bg) => [0, 1, 2].map((k) => fg[k] * fg[3] + bg[k] * (1 - fg[3])).concat(1);
const lum = ([r, g, b]) => {
  const ch = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
};
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

// [foreground, background, minimum, what it is]; `page` is what an alpha background sits on
const TEXT = 4.5, EDGE = 3;
const PAIRS = [
  ["--ink", "--steel", TEXT, "body text on the page"],
  ["--ink", "--surface", TEXT, "text on a card"],
  ["--ink-2", "--surface", TEXT, "secondary text"],
  ["--ink-soft", "--steel", TEXT, "quiet text on the page"],
  ["--ink-soft", "--surface-2", TEXT, "quiet text on an inset fill"],
  ["--ink-muted", "--surface", TEXT, "the quietest text"],
  ["--ink-muted", "--surface-2", TEXT, "the quietest text on an inset fill"],
  ["--on-primary", "--primary", TEXT, "primary button"],
  ["--on-primary", "--primary-hover", TEXT, "primary button, hover"],
  ["--on-primary", "--primary-pressed", TEXT, "primary button, pressed"],
  ["--primary-text", "--steel", TEXT, "a link on the page"],
  ["--primary-text", "--surface-2", TEXT, "a link on an inset fill"],
  ["--primary-text", "--primary-wash", TEXT, "a teal pill (Now, In progress)"],
  ["--primary-hover", "--steel", TEXT, "a link, hover"],
  ["--primary-fill", "--steel", EDGE, "progress, toggles, checkboxes, the active tab bar"],
  ["--primary-fill", "--surface", EDGE, "progress on a card"],
  ["--focus", "--steel", EDGE, "the focus ring"],
  ["--focus", "--panel", EDGE, "the focus ring on a panel"],
  ["--success", "--success-wash", TEXT, "success pill"],
  ["--success", "--surface", TEXT, "success text"],
  ["--warning", "--warning-wash", TEXT, "warning pill"],
  ["--warning", "--surface", TEXT, "warning text"],
  ["--danger", "--danger-wash", TEXT, "danger pill and errors"],
  ["--danger", "--surface", TEXT, "danger text"],
  ["--info", "--info-wash", TEXT, "info pill (in review, scheduled)"],
  ["--secondary", "--info-wash", TEXT, "your role pill (navy)"],
  ["--kt-strong", "--kt-wash", TEXT, "the KT badge"],
  ["--kt", "--surface", TEXT, "KT text and marks"],
  ["--steel", "--ink", TEXT, "a toast (ink)"],
];
// inside a panel (navy): the scope's own tokens over the theme's
const PANEL_PAIRS = [
  ["--ink", "--panel", TEXT, "text on a panel"],
  ["--ink", "--panel-2", TEXT, "text on a panel's header strip"],
  ["--ink", "--field", TEXT, "text in an input on a panel"],
  ["--panel-ink-soft", "--panel", TEXT, "quiet text and labels on a panel"],
  ["--panel-ink-soft", "--panel-2", TEXT, "quiet text on a panel's strip"],
  ["--panel-ink-soft", "--field", TEXT, "a placeholder in an input on a panel"],
  ["--primary-text", "--panel", TEXT, "a link on a panel"],
  ["--secondary", "--panel", TEXT, "a panel's navy eyebrow"],
  ["--field-line", "--panel", EDGE, "an input's edge against the panel"],
  ["--field-line", "--field", EDGE, "an input's edge against its own fill"],
];
const RAIL_PAIRS = [
  ["--rail-text", "--rail", TEXT, "rail links"],
  ["--rail-mute", "--rail", TEXT, "rail labels"],
  ["--rail-accent", "--rail", EDGE, "the active page's teal bar and icon"],
  ["--rail-bright", "--rail-active", TEXT, "the active page's label on its teal tint"],
  ["--badge-ink", "--rail-accent", TEXT, "the unread count"],
];

let failed = 0;
function check(theme, tokens, pairs, page) {
  for (const [fg, bg, min, what] of pairs) {
    const base = resolve(tokens, page);
    const b = over(resolve(tokens, bg), base);
    const f = over(resolve(tokens, fg), b);
    const r = ratio(f, b);
    const ok = r >= min;
    if (!ok) failed++;
    console.log(`${ok ? "PASS" : "FAIL"} ${r.toFixed(2).padStart(5)}:1  ${theme.padEnd(12)} ${fg} on ${bg}`.padEnd(78) + ` (min ${min}) ${what}`);
  }
}
for (const [theme, tokens] of [["light", light], ["dark", dark]]) {
  check(theme, tokens, PAIRS, "--steel");
  const scoped = { ...tokens };
  for (const [k, v] of Object.entries(panel)) scoped[k] = v;
  check(`${theme} panel`, scoped, PANEL_PAIRS, "--panel");
  check(`${theme} rail`, { ...tokens, ...rail }, RAIL_PAIRS, "--rail");
}
console.log(failed ? `\n${failed} pair(s) below the minimum.` : "\nEvery pair meets WCAG AA.");
process.exit(failed ? 1 : 0);
