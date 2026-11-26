/** Navigation icons for the lifecycle pages, in the same stroke style as ui.tsx's Icon (24px grid, after Lucide, ISC
 *  licence). NavIcon draws these, and falls back to ui.tsx's set for every other name. */
import { Icon, IconName } from "../ui";

const EXTRA = {
  home: ["M3 10.5 12 3l9 7.5", "M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5"],
  team: ["M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M23 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  enrol: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M19 8v6", "M22 11h-6"],
  chart: ["M3 20h18", "M6 20v-8", "M11 20V5", "M16 20v-5", "M21 20V9"],
  layers: ["M12 2 2 7l10 5 10-5-10-5z", "M2 17l10 5 10-5", "M2 12l10 5 10-5"],
  list: ["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"],
  handover: ["M17 1l4 4-4 4", "M3 11V9a4 4 0 0 1 4-4h14", "M7 23l-4-4 4-4", "M21 13v2a4 4 0 0 1-4 4H3"],
  feedback: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z", "M12 7v4", "M12 14h.01"],
  setup: ["M3 6l1.5 1.5L7 5", "M3 12l1.5 1.5L7 11", "M3 18l1.5 1.5L7 17", "M11 6h10", "M11 12h10", "M11 18h10"],
  people: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M19 21v-2a4 4 0 0 0-1.5-3.1", "M15.5 3.3a4 4 0 0 1 0 7.4"],
  eye: ["M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  clock: ["M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z", "M12 7v5l3 2"],
  download: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "M7 10l5 5 5-5", "M12 15V3"],
  program: ["M3 12h4l3 8 4-16 3 8h4"],
  board: ["M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z", "M9 3v18", "M15 3v18", "M5.5 7h2", "M11 7h2", "M11 11h2", "M16.5 7h2"],
  agents: ["M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z", "M5 3.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z", "M19 3.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
    "M5 16.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z", "M19 16.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z", "M6.5 7l3.7 3.4", "M17.5 7l-3.7 3.4", "M6.5 17l3.7-3.4", "M17.5 17l-3.7-3.4"],
};
export type NavIconName = IconName | keyof typeof EXTRA;

export function NavIcon({ name, size = 18 }: { name: NavIconName; size?: number }) {
  const paths = (EXTRA as Record<string, string[]>)[name];
  if (!paths) return <Icon name={name as IconName} size={size} />;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths.map((d) => <path key={d} d={d} />)}
    </svg>
  );
}
