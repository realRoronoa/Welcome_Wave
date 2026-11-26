/** The agents page's diagram: the Supervisor in the centre, the specialists on a ring around it, and Approvals as the one
 *  way out, where people decide. Spokes carry the events the Supervisor sends to each agent; dashed arcs are the chains it
 *  runs from one agent to the next (edges[]). Every agent, chain and the exit can be reached with Tab: focus or hover names
 *  its events. Below 640 px the drawing gives way to a list in the same order. Styles: styles/agents.css. */
import { KeyboardEvent, useId, useMemo, useState } from "react";
import { Agent, AgentEdge, eventLabels } from "./agentModel";
import { plural } from "./kit";

const W = 1000, H = 430, CX = 400, CY = 215, RX = 300, RY = 152;
const NODE = { w: 156, h: 52 }, HUB = { w: 176, h: 62 }, EXIT = { x: 884, y: CY, w: 150, h: 62 };
/** Ring order, by angle from the right going anticlockwise: the knowledge agents across the top, the program's across the
 *  bottom, and every chain between neighbours (Librarian → Keeper, Note-taker → Planner, Coach → Planner), never across the
 *  line to Approvals. */
const ORDER = ["reviewer", "keeper", "librarian", "guide", "scout", "note_taker", "planner", "coach"];

type P = { x: number; y: number };
type Hot = { kind: "agent"; key: string } | { kind: "chain"; i: number } | { kind: "exit" } | { kind: "hub" } | null;

/** Where the line from a box's centre towards `to` leaves the box (plus a small gap). */
function edgeOf(c: P, half: { w: number; h: number }, to: P, gap = 5): P {
  const dx = to.x - c.x, dy = to.y - c.y;
  if (!dx && !dy) return c;
  const t = Math.min(dx ? (half.w / 2) / Math.abs(dx) : Infinity, dy ? (half.h / 2) / Math.abs(dy) : Infinity);
  const len = Math.hypot(dx, dy);
  return { x: c.x + dx * t + (dx / len) * gap, y: c.y + dy * t + (dy / len) * gap };
}
const pct = (p: P) => ({ left: `${(p.x / W) * 100}%`, top: `${(p.y / H) * 100}%` });

type Props = { agents: Agent[]; edges: AgentEdge[]; pendingTotal: number; canApprove: boolean; onOpen: (key: string) => void };

export default function AgentMap({ agents, edges, pendingTotal, canApprove, onOpen }: Props) {
  const [hot, setHot] = useState<Hot>(null);
  const uid = useId().replace(/:/g, ""); // ids for each node's description (a node is named by the words it shows)
  const hub = agents.find((a) => a.key === "supervisor");
  const ring = useMemo(() => {
    const rest = agents.filter((a) => a.key !== "supervisor");
    const ordered = [...ORDER.map((k) => rest.find((a) => a.key === k)).filter(Boolean) as Agent[], ...rest.filter((a) => !ORDER.includes(a.key))];
    const n = Math.max(ordered.length, 1);
    return ordered.map((a, i) => {
      const t = ((360 / n) * (i + 0.5) * Math.PI) / 180;
      return { a, p: { x: CX + RX * Math.cos(t), y: CY - RY * Math.sin(t) } };
    });
  }, [agents]);
  const at = (key: string) => ring.find((r) => r.a.key === key)?.p ?? null;
  const nameOf = (key: string) => agents.find((a) => a.key === key)?.name ?? key;

  // events the Supervisor sends each agent: its triggers, plus any edge drawn from the Supervisor itself
  const routed = (a: Agent) => eventLabels([...a.triggers.filter((t) => t !== "all"), ...edges.filter((e) => e.from === "supervisor" && e.to === a.key).map((e) => e.via)]);
  const hub0: P = { x: CX, y: CY };
  // chains: an arc outside the ring from one agent to the next, or a small loop when the Supervisor hands an agent's result
  // back to the same agent (a pull request matched, then reviewed)
  const chains = edges.map((e, i) => ({ e, i })).filter(({ e }) => e.from !== "supervisor" && e.to !== "supervisor" && at(e.from) && at(e.to))
    .map(({ e, i }) => {
      const a = at(e.from)!, b = at(e.to)!;
      if (e.from === e.to) {
        const dx = a.x - CX, dy = a.y - CY, len = Math.hypot(dx, dy) || 1, ox = dx / len, oy = dy / len;
        const o = edgeOf(a, NODE, { x: a.x + ox * 400, y: a.y + oy * 400 }, 3);
        const px = -oy * 13, py = ox * 13;
        const s = { x: o.x + px, y: o.y + py }, t = { x: o.x - px, y: o.y - py };
        const k = 50;
        return { e, i, d: `M${s.x} ${s.y} C${s.x + ox * k} ${s.y + oy * k} ${t.x + ox * k} ${t.y + oy * k} ${t.x} ${t.y}`, tip: { x: o.x + ox * 40, y: o.y + oy * 40 } };
      }
      const m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const dx = m.x - CX, dy = m.y - CY, len = Math.hypot(dx, dy) || 1;
      const push = 34 + Math.hypot(b.x - a.x, b.y - a.y) * 0.22;
      const c = { x: m.x + (dx / len) * push, y: m.y + (dy / len) * push };
      const s = edgeOf(a, NODE, c, 4), t = edgeOf(b, NODE, c, 6);
      return { e, i, d: `M${s.x} ${s.y} Q${c.x} ${c.y} ${t.x} ${t.y}`, tip: { x: (s.x + 2 * c.x + t.x) / 4, y: (s.y + 2 * c.y + t.y) / 4 } };
    });
  const chainText = (e: AgentEdge) => e.from === e.to ? `${nameOf(e.from)}, then ${nameOf(e.to)} again` : `${nameOf(e.from)}, then ${nameOf(e.to)}`;

  const lit = (key: string) => hot?.kind === "agent" ? hot.key === key
    : hot?.kind === "chain" ? chains.some(({ e, i }) => i === hot.i && (e.from === key || e.to === key)) : false;
  const dim = hot !== null && hot.kind !== "hub";

  const keyOpen = (key: string) => (ev: KeyboardEvent) => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onOpen(key); }
  };
  const focusable = (h: Hot) => ({
    onMouseEnter: () => setHot(h), onMouseLeave: () => setHot(null), onFocus: () => setHot(h), onBlur: () => setHot(null),
  });

  // the label shown for what is hot, placed beside it
  let tip: { p: P; below: boolean; title: string; lines: string[] } | null = null;
  if (hot?.kind === "agent") {
    const r = ring.find((x) => x.a.key === hot.key);
    if (r) {
      const ev = routed(r.a);
      tip = { p: r.p, below: r.p.y > CY, title: r.a.name, lines: [r.a.role, ev.length ? `When: ${ev.join(" · ")}` : ""].filter(Boolean) };
    }
  } else if (hot?.kind === "chain") {
    const c = chains.find((x) => x.i === hot.i);
    if (c) tip = { p: c.tip, below: c.tip.y > CY, title: eventLabels([c.e.via])[0], lines: [chainText(c.e)] };
  } else if (hot?.kind === "exit") {
    tip = { p: { x: EXIT.x, y: EXIT.y - EXIT.h / 2 }, below: false, title: "Approvals",
      lines: [pendingTotal ? `${plural(pendingTotal, "proposal")} waiting for a person` : "Every proposal waits here for a person"] };
  } else if (hot?.kind === "hub" && hub) {
    tip = { p: { x: CX, y: CY + HUB.h / 2 }, below: true, title: hub.name, lines: [hub.role] };
  }

  return (
    <div className="ag-map">
      <div className="ag-map-draw">
        <svg className="ag-map-svg" viewBox={`0 0 ${W} ${H}`} role="group" aria-label="How the agents work together">
          <defs>
            <marker id="ag-arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M1.5 1.5L8.5 5L1.5 8.5" className="ag-arrow" />
            </marker>
            <marker id="ag-arrow-in" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M1.5 1.5L8.5 5L1.5 8.5" className="ag-arrow is-ink" />
            </marker>
          </defs>

          {/* spokes: the Supervisor to each agent */}
          {ring.map(({ a, p }) => {
            const s = edgeOf(hub0, HUB, p), e = edgeOf(p, NODE, hub0);
            return <line key={a.key} className={`ag-spoke${lit(a.key) ? " is-lit" : ""}${dim && !lit(a.key) ? " is-dim" : ""}`} x1={s.x} y1={s.y} x2={e.x} y2={e.y} />;
          })}

          {/* chains the Supervisor runs from one agent to the next */}
          {chains.map(({ e, i, d }) => {
            const on = (hot?.kind === "chain" && hot.i === i) || (hot?.kind === "agent" && (e.from === hot.key || e.to === hot.key));
            return (
              <g key={i} className={`ag-chain${on ? " is-lit" : ""}${dim && !on ? " is-dim" : ""}`} tabIndex={0} role="img"
                 aria-label={`${eventLabels([e.via])[0]}: ${chainText(e)}`} {...focusable({ kind: "chain", i })}>
                <path className="ag-chain-hit" d={d} />
                <path className="ag-chain-line" d={d} markerEnd={on ? "url(#ag-arrow-in)" : "url(#ag-arrow)"} />
              </g>
            );
          })}

          {/* the one way out */}
          <line className={`ag-exit-line${hot?.kind === "exit" ? " is-lit" : ""}`} x1={CX + HUB.w / 2 + 5} y1={CY} x2={EXIT.x - EXIT.w / 2 - 6} y2={CY} markerEnd="url(#ag-arrow-in)" />

          {/* the Supervisor */}
          <g className="ag-node is-hub" tabIndex={0} role="button" aria-describedby={hub ? `${uid}-hub-d` : undefined}
             onKeyDown={keyOpen("supervisor")} onClick={() => onOpen("supervisor")} {...focusable({ kind: "hub" })}>
            <rect className="ag-ring" x={CX - HUB.w / 2 - 5} y={CY - HUB.h / 2 - 5} width={HUB.w + 10} height={HUB.h + 10} rx="16" />
            <rect className="ag-frame" x={CX - HUB.w / 2} y={CY - HUB.h / 2} width={HUB.w} height={HUB.h} rx="12" />
            <text className="ag-name" x={CX} y={CY - 3} textAnchor="middle">{hub?.name ?? "Supervisor"}</text>
            <text className="ag-sub" x={CX} y={CY + 15} textAnchor="middle">Every event</text>
          </g>

          {/* the specialists */}
          {ring.map(({ a, p }) => {
            const sub = [`${a.runs_7d} run${a.runs_7d === 1 ? "" : "s"}`, a.pending ? `${a.pending} waiting` : ""].filter(Boolean).join(" · ");
            return (
              <g key={a.key} className={`ag-node${lit(a.key) ? " is-lit" : ""}${dim && !lit(a.key) ? " is-dim" : ""}${a.pending ? " has-pending" : ""}`}
                 tabIndex={0} role="button" aria-describedby={`${uid}-${a.key}-d`}
                 onKeyDown={keyOpen(a.key)} onClick={() => onOpen(a.key)} {...focusable({ kind: "agent", key: a.key })}>
                <rect className="ag-ring" x={p.x - NODE.w / 2 - 5} y={p.y - NODE.h / 2 - 5} width={NODE.w + 10} height={NODE.h + 10} rx="14" />
                <rect className="ag-frame" x={p.x - NODE.w / 2} y={p.y - NODE.h / 2} width={NODE.w} height={NODE.h} rx="10" />
                <text className="ag-name" x={p.x} y={p.y - 3} textAnchor="middle">{a.name}</text>
                <text className="ag-sub" x={p.x} y={p.y + 14} textAnchor="middle">{sub}</text>
              </g>
            );
          })}

          {/* Approvals: where people decide */}
          {(() => {
            const body = (
              <>
                <rect className="ag-ring" x={EXIT.x - EXIT.w / 2 - 5} y={EXIT.y - EXIT.h / 2 - 5} width={EXIT.w + 10} height={EXIT.h + 10} rx="16" />
                <rect className="ag-frame" x={EXIT.x - EXIT.w / 2} y={EXIT.y - EXIT.h / 2} width={EXIT.w} height={EXIT.h} rx="12" />
                <text className="ag-name" x={EXIT.x} y={EXIT.y - 3} textAnchor="middle">Approvals</text>
                <text className="ag-sub" x={EXIT.x} y={EXIT.y + 15} textAnchor="middle">{pendingTotal ? `${pendingTotal} waiting` : "People decide"}</text>
              </>
            );
            const named = { "aria-describedby": `${uid}-exit-d` }; // named by what it shows; what it does as its description
            return canApprove
              ? <a href="#/approvals" className="ag-node is-exit" {...named} {...focusable({ kind: "exit" })}>{body}</a>
              : <g className="ag-node is-exit" tabIndex={0} role="img" aria-label={`Approvals, ${pendingTotal ? `${pendingTotal} waiting` : "people decide"}`} {...named} {...focusable({ kind: "exit" })}>{body}</g>;
          })()}
        </svg>
        {/* each node's description (what it does), read after its name; hidden, so it is not part of what the node shows */}
        <div hidden>
          {hub && <span id={`${uid}-hub-d`}>{hub.role}</span>}
          {ring.map(({ a }) => <span key={a.key} id={`${uid}-${a.key}-d`}>{a.role}. Opens its details.</span>)}
          <span id={`${uid}-exit-d`}>{pendingTotal ? `${plural(pendingTotal, "proposal")} for people to decide.` : "People decide. Nothing waiting."}</span>
        </div>
        {tip && (
          <div className={`ag-tip${tip.below ? " below" : ""}`} style={pct(tip.p)} aria-hidden="true">
            <p className="ag-tip-t">{tip.title}</p>
            {tip.lines.map((l, i) => <p key={i} className="ag-tip-l">{l}</p>)}
          </div>
        )}
      </div>

      {/* phones: the same order as a list */}
      <ol className="ag-map-list" aria-label="How the agents work together">
        {hub && (
          <li className="is-hub">
            <button type="button" onClick={() => onOpen(hub.key)}><span className="ag-l-name">{hub.name}</span><span className="ag-l-sub">{hub.role}</span></button>
          </li>
        )}
        {agents.filter((a) => a.key !== "supervisor").map((a) => {
          const then = chains.filter(({ e }) => e.from === a.key);
          return (
            <li key={a.key}>
              <button type="button" onClick={() => onOpen(a.key)}>
                <span className="ag-l-name">{a.name}{a.pending ? <span className="ag-l-n">{a.pending} waiting</span> : null}</span>
                {then.map(({ e, i }) => <span key={i} className="ag-l-then">Then {nameOf(e.to)}{e.from === e.to ? " again" : ""} · {eventLabels([e.via])[0]}</span>)}
              </button>
            </li>
          );
        })}
        <li className="is-exit">
          {canApprove
            ? <a href="#/approvals"><span className="ag-l-name">Approvals</span><span className="ag-l-sub">{pendingTotal ? `${plural(pendingTotal, "proposal")} waiting. People decide.` : "People decide."}</span></a>
            : <span className="ag-l-static"><span className="ag-l-name">Approvals</span><span className="ag-l-sub">People decide.</span></span>}
        </li>
      </ol>
    </div>
  );
}
