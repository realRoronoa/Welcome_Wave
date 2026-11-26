/** Dates, names and the first week for the pre-boarding page. */
import type { PortalData } from "./types";

/** A YYYY-MM-DD date is a local calendar day (never shifted by the time zone); a timestamp is read as it is. */
function parse(iso: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(iso);
}
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export const longDay = (iso: string) => parse(iso).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
export const shortDay = (iso: string) => parse(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
export const dayMonth = (iso: string) => parse(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
export const shortTime = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

/** "Priya (Manager)" is Priya: the role is said beside the name. */
export const plainName = (name: string | null) => (name ? name.replace(/\s*\(.*?\)/g, "").trim() || name : null);

export type Day = { date: string; label: string; title: string; text: string; done: boolean; today: boolean };

/** The first week to show. When the server sends this hire's own (`first_week`: the template's first-week milestones and the KT
 *  sessions booked for them) that is it. Otherwise a generic week, said to be one: nothing here is a promise about this hire. */
export function firstWeek(d: PortalData): { days: Day[]; typical: boolean } {
  const own = (d.first_week ?? []).filter((x) => x && x.date && x.title);
  if (own.length === 0) return { days: typicalWeek(d), typical: true };
  const today = ymd(new Date()), start = parse(d.start_date).getTime();
  const days = [...own].sort((a, b) => a.date.localeCompare(b.date)).map((x) => ({
    date: x.date, label: `Day ${Math.max(1, Math.round((parse(x.date).getTime() - start) / 864e5) + 1)}`, title: x.title, text: x.text ?? "",
    done: !!x.done, today: x.date === today,
  }));
  return { days, typical: false };
}

/** The first five working days from the start date. Only what this page knows is promised: the people, the team, the machine
 *  check and the way every program starts (the code tour, KT sessions, a first task chosen with the manager). */
function typicalWeek(d: PortalData): Day[] {
  const manager = plainName(d.manager), mentor = plainName(d.mentor);
  const plan = [
    { title: "Welcome", text: manager ? `Meet ${manager}, your manager, and get your accounts.` : "Meet your manager and get your accounts." },
    { title: "Set up your machine", text: "Run the environment check until every tool passes.", done: !!d.environment.latest?.ok },
    { title: mentor ? `Meet ${mentor}` : "Meet your mentor", text: d.team ? `How the ${d.team} team works, and where to ask.` : "How the team works, and where to ask." },
    { title: "Learn the code", text: "Take the code tour and watch the KT sessions for your area." },
    { title: "Choose a first task", text: manager ? `Pick it with ${manager}.` : "Pick it with your manager." },
  ];
  const today = ymd(new Date());
  const days: Day[] = [];
  const at = parse(d.start_date);
  while (days.length < plan.length) {
    if (at.getDay() !== 0 && at.getDay() !== 6) {
      const p = plan[days.length];
      const date = ymd(at);
      days.push({ date, label: `Day ${days.length + 1}`, title: p.title, text: p.text, done: !!p.done, today: date === today });
    }
    at.setDate(at.getDate() + 1);
  }
  return days;
}
