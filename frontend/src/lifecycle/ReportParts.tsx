/** A report figure as a tile: the current cohort, the previous one beside it, "not enough data" said plainly, and how
 *  the figure is worked out behind a disclosure. Reports and the executive's home share it. */
import { How } from "./kit";
import type { ReportFigure } from "./types";

const thin = (display: string) => /not enough data/i.test(display);

function delta(f: ReportFigure) {
  const a = f.current.value, b = f.previous.value;
  if (a === null || b === null || a === b) return null;
  const d = a - b;
  const unit = f.unit === "%" ? " pts" : f.unit === "days" ? " d" : f.unit === "hours" ? " h" : "";
  const n = Math.abs(d) >= 10 || Number.isInteger(d) ? Math.round(Math.abs(d)) : Math.abs(d).toFixed(1);
  return { up: d > 0, text: `${n}${unit}` };
}

/** "74 %" → ["74", "%"], "19.5 days" → ["19.5", "days"]: the number large, the unit small. */
function parts(display: string): [string, string] {
  const m = /^(-?[\d.,]+)\s*(.*)$/.exec(display.trim());
  return m ? [m[1], m[2]] : [display, ""];
}

export function FigureTile({ f }: { f: ReportFigure }) {
  const now = f.current, before = f.previous;
  const dl = delta(f);
  const [num, unit] = parts(now.display);
  return (
    <div className="lc-tile">
      <div className="lc-tile-top">
        <p className="lc-tile-label">{f.label}</p>
        <How text={f.how} align="end" />
      </div>
      {thin(now.display)
        ? <p className="lc-tile-value thin">Not enough data</p>
        : <p className="lc-tile-value">{num}{unit && <span className="lc-tile-unit">{unit}</span>}</p>}
      <p className="lc-tile-foot">
        <span>Previous <strong>{thin(before.display) ? "not enough data" : before.display}</strong></span>
        {dl && <span className={`lc-delta ${dl.up ? "up" : "down"}`} aria-label={`${dl.up ? "up" : "down"} ${dl.text}`}>
          <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden="true"><path d={dl.up ? "M5 1.5 9 8H1z" : "M5 8.5 9 2H1z"} /></svg>{dl.text}
        </span>}
        <span className="lc-tile-n" title="How many hires, cases or answers the figure is based on">n = {now.n}</span>
      </p>
    </div>
  );
}
