/* Building blocks shared by the site's pages: the grid placement helper, word-masked headings, and the cover headline's
   words and full stop. Classes are s-prefixed and styled in site.css (scoped under .site). */
import { CSSProperties, Fragment, ReactNode } from "react";

/** Grid placement: `lg` from 1100 px (12 columns), `md` from 600 px (6 columns); phones (4 columns) span the row unless `sm`. */
export function col(lg: string, md?: string, sm?: string): CSSProperties {
  const s: Record<string, string> = { "--c": lg };
  if (md) s["--cm"] = md;
  if (sm) s["--cs"] = sm;
  return s as CSSProperties;
}

/** A heading whose words rise out of a mask, one after another (site.css .s-w; the stagger stops at 12 steps). The words are
 *  set plain: at the display voice's weight 600 no pair (rt, lt) needs a spacing patch. */
export function Words({ text }: { text: string }) {
  const words = text.split(" ");
  return (
    <>
      {words.map((w, i) => (
        <Fragment key={i}>
          <span className="s-w"><span style={{ "--i": Math.min(i, 11) } as CSSProperties}>{w}</span></span>
          {i < words.length - 1 ? " " : null}
        </Fragment>
      ))}
    </>
  );
}

/** One word of the cover's headline in its mask (site.css .s-hw). `l`: the line it sets on, "phone tablet-and-up" (the rise
 *  is staggered per line); `h`: its first letter's left side bearing when it starts a line from 600 px, `hs` on phones (the
 *  word then hangs by that much, so the ink, not the advance, sits on the column edge). Geist 600: L .08, c .039, f .051,
 *  w .021 em; re-measure if the weight changes. */
export function Hw({ l, h, hs, children }: { l: string; h?: string; hs?: string; children: ReactNode }) {
  const [sm, md] = l.split(" ");
  const s: Record<string, string | undefined> = { "--l-sm": sm, "--l-md": md, "--hang": h, "--hang-sm": hs ?? h };
  return <span className="s-hw" style={s as CSSProperties}><span>{children}</span></span>;
}

/** The headline's full stop: one stroke of the mark (docs/brand/BRAND.md), the first one, the stroke the colour lockup paints
 *  teal: a capsule leaning at the mark's angle, set on the baseline at a full stop's size. At the entrance the pen draws it
 *  down its own slant, top first, as the intro draws the mark (site.css .s-h1-pen). The "." in front of it (set at size 0, so
 *  it takes no room) is the full stop of a copy of the headline, and of its text. */
export function Stop() {
  return (
    <>
      <span className="s-h1-dot">.</span>
      <span className="s-h1-stop" aria-hidden="true"><span className="s-h1-pen"><span className="s-h1-cap" /></span></span>
    </>
  );
}
