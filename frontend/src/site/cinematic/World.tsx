/* The product world: real product panels (product/Panels.tsx) floating in a dark, foggy space with a faint grid floor,
   which the camera travels through as the film scrolls (ScrollStoryController.ts moves the camera and each panel; the
   geometry is geometry.ts). Teal threads, drawn on a canvas over the world, tie each recorded KT moment to what it explains.
   The product's words stay in the DOM for assistive technology: the world is a labelled group, after the film's words. */
import { forwardRef, ReactNode } from "react";
import type { PanelId } from "./geometry";
import { AnswerPanel, ClipPanel, CodePanel, DetailPanel, PathPanel, RecPanel, ReportPanel, StepsPanel } from "./product/Panels";

const PANELS: [PanelId, ReactNode][] = [
  ["code", <CodePanel />],
  ["rec", <RecPanel />],
  ["answer", <AnswerPanel />],
  ["path", <PathPanel />],
  ["clip", <ClipPanel shot="first-contribution" chip="Merged · engine/policy.py" />],
  ["steps", <StepsPanel />],
  ["detail", <DetailPanel />],
  ["team", <ClipPanel shot="team-impact" />],
  ["report", <ReportPanel />],
];

export const World = forwardRef<HTMLDivElement>(function World(_, ref) {
  return (
    <div ref={ref} className="w-view" role="group" aria-label="Welcome Wave with the demo project: a recorded KT explanation linked to the exact lines of code it covers, a verified answer that cites both, the new engineer's learning path, his first contribution step by step, and the leadership report (illustrative figures).">
      {/* the world breathes while it is on screen (a slow drift of the whole picture, cinematic.css w-drift), so a rest at a
          station is never a still */}
      <div className="w-drift">
      <div className="w-camera">
        {/* the threads' light runs under the panels: a panel always covers it, never the other way round */}
        <canvas className="w-threads-under" aria-hidden="true" />
        <div className="w-world">
          <div className="w-floor" aria-hidden="true" />
          {PANELS.map(([id, node]) => (
            <div key={id} className={`w-panel w-${id}`} data-panel={id}>{node}</div>
          ))}
        </div>
      </div>
      <canvas className="w-threads" aria-hidden="true" />{/* the threads' end points, above */}
      </div>
      <div className="w-fog" aria-hidden="true" />
    </div>
  );
});
