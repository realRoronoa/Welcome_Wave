// Demo mode's session: the signed-in member's id, sent as X-Demo-User. A stored id means signed in; none means the sign-in page.
const DEMO_USER_KEY = "ww-demo-user";
let demoUser: string | null = null;
try { demoUser = localStorage.getItem(DEMO_USER_KEY) || null; } catch { /* storage blocked: sign in for this page only */ }
let demo = false; // set from GET /api/config before anything else is rendered
let idToken: string | null = null; // kept current by Identity Platform's onIdTokenChanged
let tokenSource: ((forceRefresh: boolean) => Promise<string | null>) | null = null;

export function setMode(isDemo: boolean) { demo = isDemo; }
/** Demo mode: remember who signed in (POST /api/demo/login answered with them), or forget them with null. */
export function setDemoUser(id: string | null) {
  demoUser = id;
  try { if (id) localStorage.setItem(DEMO_USER_KEY, id); else localStorage.removeItem(DEMO_USER_KEY); } catch { /* private mode */ }
}
export function getDemoUser() { return demoUser; }
/** Demo mode's sign-out: the next page load (and this one) shows the sign-in page. */
export function demoSignOut() { setDemoUser(null); }
export function setIdToken(t: string | null) { idToken = t; }
/** Production: asked for a fresh ID token before every request (Firebase caches it and refreshes it near expiry). */
export function setTokenSource(fn: typeof tokenSource) { tokenSource = fn; }

export class ApiError extends Error {
  /** `detail` is the server's structured detail object when it sent one (e.g. the pending invitations). */
  constructor(public status: number, message: string, public code?: string, public requestId?: string, public detail?: any) { super(message); }
}

async function send(path: string, init: RequestInit, refresh: boolean) {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (typeof init.body === "string") headers["Content-Type"] = "application/json"; // FormData sets its own boundary
  if (demo) { if (demoUser) headers["X-Demo-User"] = demoUser; }
  else {
    const token = tokenSource ? await tokenSource(refresh).catch(() => idToken) : idToken;
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  return fetch(`/api${path}`, { ...init, headers });
}

async function toError(res: Response) {
  let body: any = null;
  try { body = await res.json(); } catch { /* not json, e.g. a proxy error page */ }
  const d = body?.detail;
  let message = res.status >= 502 ? "The server is not responding. Try again in a moment." : `Request failed (${res.status})`;
  let code: string | undefined, detail: any;
  if (typeof d === "string") { message = d; if (/^[a-z]+(_[a-z]+)+$/.test(d)) code = d; } // a bare code, e.g. "plan_in_progress"
  else if (Array.isArray(d)) message = d.map((x) => `${Array.isArray(x?.loc) ? x.loc[x.loc.length - 1] : "input"}: ${x?.msg}`).join("; ") || message; // 422 validation
  else if (d && typeof d === "object") { code = d.code; message = d.message ?? message; detail = d; }
  return new ApiError(res.status, message, code, body?.request_id ?? res.headers.get("x-request-id") ?? undefined, detail);
}

/** The live-updates stream (src/live.ts): a streamed GET with the same sign-in headers as every call. `topics` narrows it
 *  (one agent run's steps: "agent:<run_id>"). */
export async function liveRequest(signal: AbortSignal, topics?: string) {
  const init: RequestInit = { signal, headers: { Accept: "text/event-stream" }, cache: "no-store" };
  const path = topics ? `/live?topics=${encodeURIComponent(topics)}` : "/live";
  let res = await send(path, init, false);
  if (res.status === 401 && !demo && tokenSource) res = await send(path, init, true);
  return res;
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  let res = await send(path, init, false);
  if (res.status === 401 && !demo && tokenSource) res = await send(path, init, true); // token expired: refresh once
  if (!res.ok) throw await toError(res);
  const text = res.status === 204 ? "" : await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Save a file the API sends (it needs the same sign-in headers as every call, so a plain link would not do). */
export async function download(path: string, fallbackName: string) {
  const res = await send(path, {}, false);
  if (!res.ok) throw await toError(res);
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Upload a (large) file with progress: XMLHttpRequest, because fetch cannot report how much of a body has been sent. */
export async function upload<T = any>(path: string, form: FormData, onProgress?: (fraction: number) => void): Promise<T> {
  const headers: Record<string, string> = {};
  if (demo) { if (demoUser) headers["X-Demo-User"] = demoUser; }
  else {
    const token = tokenSource ? await tokenSource(false).catch(() => idToken) : idToken;
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  const res = await new Promise<Response>((done, fail) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onload = () => done(new Response(xhr.responseText || null, {
      status: xhr.status, headers: { "content-type": xhr.getResponseHeader("content-type") ?? "application/json" } }));
    xhr.onerror = () => fail(new ApiError(0, "The upload was interrupted. Check your connection and try again."));
    xhr.send(form);
  });
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

export const post = <T = any>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T = any>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = (path: string) => api<void>(path, { method: "DELETE" });

const AUTH_ERRORS: Record<string, string> = {
  "auth/invalid-credential": "That email and password do not match.",
  "auth/wrong-password": "That email and password do not match.",
  "auth/user-not-found": "That email and password do not match.",
  "auth/invalid-email": "Enter a valid email address.",
  "auth/missing-password": "Enter your password.",
  "auth/email-already-in-use": "An account with this email already exists. Sign in instead.",
  "auth/weak-password": "Choose a password with at least 6 characters.",
  "auth/too-many-requests": "Too many attempts. Wait a minute and try again.",
  "auth/popup-blocked": "Your browser blocked the sign-in window. Allow pop-ups for this site and try again.",
  "auth/network-request-failed": "Could not reach the sign-in service. Check your connection.",
  "auth/user-disabled": "This account has been disabled.",
  "auth/operation-not-allowed": "This sign-in method is not enabled for this site.",
  "auth/unauthorized-domain": "This domain is not authorised for sign-in. Ask an administrator to add it in Identity Platform.",
  "auth/account-exists-with-different-credential": "This email already signs in another way. Use that method instead.",
};

/** The one place that turns any thrown error into a sentence a person can act on. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const ref = e.requestId && e.status >= 500 ? ` (reference ${e.requestId})` : "";
    if (e.status === 401) return "Your session has expired. Sign in again.";
    if (e.status === 413) {
      const mb = e.message.match(/(\d+) MB/)?.[1];
      return `That file is too large.${mb ? ` The limit is ${mb} MB.` : ""}`;
    }
    if (e.status === 429) return "Too many requests in a minute. Wait a moment and try again.";
    if (e.code === "deactivated" && /^Request failed/.test(e.message)) return "This account has been deactivated. Ask an admin of your organisation to reactivate it.";
    return e.message + ref; // a structured detail ({code, message}) arrives here as its message
  }
  const code = (e as { code?: unknown })?.code;
  if (typeof code === "string" && code.startsWith("auth/")) return AUTH_ERRORS[code] ?? "Sign-in failed. Try again.";
  if (e instanceof TypeError) return "Could not reach the server. Check your connection and try again.";
  return e instanceof Error && e.message ? e.message : "Something went wrong.";
}

export function mmss(s: number) {
  const t = Math.max(0, Math.floor(s));
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}
/** Today in the viewer's time zone, as YYYY-MM-DD. */
export function today() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
/** A calendar date. Date-only strings (YYYY-MM-DD) are read as local dates so they never shift a day. */
export function day(iso: string | null | undefined) {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const d = m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
/** How long ago, for past timestamps. */
export function when(iso: string | null | undefined) {
  if (!iso) return "";
  const d = new Date(iso), min = Math.round((Date.now() - d.getTime()) / 60000);
  if (Number.isNaN(min)) return "";
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  if (min < 24 * 60) return `${Math.round(min / 60)} h ago`;
  return day(iso);
}

export type Role = "manager" | "new_hire" | "expert" | "admin" | "executive";
export const ROLE_LABEL: Record<string, string> = { new_hire: "New hire", expert: "Expert", manager: "Manager", admin: "Admin", executive: "Executive" };
export const isManager = (r: string) => r === "manager" || r === "admin";

export type Config = {
  mode: string; env: string; firebase: { apiKey: string; authDomain: string; projectId: string; emulator?: string | null } | null;
  signup_enabled: boolean; github_app: boolean;
  ai?: boolean; // false: self-hosted without a Gemini key (chat, plans, indexing and call notes are off until one is added)
  repo_by_url?: boolean; // self-hosted: a repository is connected by its GitHub URL
  recording_upload?: boolean; // a KT session's recording can be uploaded
  google_calendar?: boolean; // Google Calendar can be connected (GET /integrations says whether this organisation has)
  calendly?: boolean; // Calendly bookings arrive by webhook
  email?: boolean; // this server sends email (invitations): the pages then say who a link was emailed to
  demo_password?: string | null; // demo only: the one password every demo account signs in with (shown on the sign-in page)
};
export type Project = { id: string; org_id: string; name: string; description: string };
export type User = {
  id: string; org_id: string; email: string; name: string; role: Role; github_login: string | null;
  status?: "active" | "deactivated"; // a deactivated member gets 403 {code: "deactivated"} from every call
};
export type Me = User & { org_name: string; projects: Project[] };

export type Citation = { chunk_id: string | null; source_type: string; label: string; ref: any };
export type KTClip = {
  meeting_id: string; meeting_title: string; file_path: string; symbol: string | null;
  start_line: number | null; end_line: number | null; start_s: number; end_s: number;
  confidence: number; method: "path" | "symbol" | "semantic"; code_changed_since: boolean;
};
export type Task = {
  id: string; title: string; description: string; kind: string; status: string; origin: string;
  due_date: string | null; acceptance_criteria: string[]; related_paths: string[]; pr_url: string | null;
  github_issue_number?: number | null; pr_number?: number | null; onboarding_id?: string;
  assignee?: string | null; // the new hire's name
  review_id?: string | null; // the newest review of its pull request
  review_pending?: boolean; // managers: a review of it waits in Approvals
};
