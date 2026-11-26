/** GET /api/preboarding/{token}: what a new hire's pre-boarding page shows (docs/specs/preboarding.md). The link is the
 *  permission, so this is all the page ever receives: names, dates, documents, access status and environment results. */
export type EnvResult = { name: string; ok: boolean; detail: string };
export type EnvCheck = { ran_at: string; ok: boolean; results: EnvResult[] };
export type Acknowledgement = { doc_key: string; title: string; acknowledged_at: string | null };
export type AccessStatus = "requested" | "granted" | "not_needed";

export type PortalData = {
  hire: { first_name: string };
  project: string;
  team: string | null;
  role_title: string | null;
  start_date: string; // YYYY-MM-DD
  days_until_start: number; // 0 once the start date has come
  manager: string | null;
  mentor: string | null;
  acknowledgements: Acknowledgement[];
  access: { system: string; status: AccessStatus | string }[];
  environment: { tools: string[]; latest: EnvCheck | null };
  expires_at: string | null;
  /** When the server sends it: this hire's own first week, from the program template's first-week milestones and the KT sessions
   *  booked for those days (each: a date, a title, and optionally one line, whether it is done, and what it is). Absent today. */
  first_week?: { date: string; title: string; text?: string | null; done?: boolean; kind?: string }[] | null;
};
