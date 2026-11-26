/** Front-end crash reports. A render crash, an uncaught error or an unhandled rejection gets a short reference id, shown to
 *  the person ("Reference 7k2f9q") and sent with a redacted report: the message, the stack's first lines, the page without
 *  its query or the hash's query (they can carry tokens or search text), and the build. Nothing typed on the page is sent.
 *
 *  Reports go to POST /api/client-errors with navigator.sendBeacon (fire and forget, also during unload); a server that
 *  does not take them simply drops them. At most MAX reports per page load, and each distinct error once. */

const URL_PATH = "/api/client-errors";
const MAX = 5;
let sent = 0;
const seen = new Set<string>();

/** A chunk of the build that will not load (a deploy replaced it, or the network dropped): a reload is the remedy. */
export function isChunkError(e: unknown): boolean {
  const err = e as { name?: string; message?: string } | null;
  return !!err && (err.name === "ChunkLoadError"
    || /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i
      .test(err.message ?? ""));
}

const refId = () => Math.random().toString(36).slice(2, 8);

/** The page without anything after "?" in the path or in the hash route. */
function where(): string {
  const hash = location.hash.split("?")[0];
  return `${location.pathname}${hash}`;
}

/** Report `error` (once per distinct message) and return the reference id to show. */
export function reportError(error: unknown, kind: "render" | "error" | "rejection", componentStack?: string): string {
  const id = refId();
  const err = error instanceof Error ? error : new Error(typeof error === "string" ? error : "Non-error thrown");
  const key = `${err.name}:${err.message}`.slice(0, 300);
  console.error(`[Welcome Wave] ${kind} error, reference ${id}:`, error);
  if (seen.has(key) || sent >= MAX) return id;
  seen.add(key);
  sent++;
  const body = JSON.stringify({
    id, kind,
    name: err.name.slice(0, 100),
    message: err.message.slice(0, 500),
    stack: (err.stack ?? "").split("\n").slice(0, 12).join("\n").slice(0, 2000),
    component: (componentStack ?? "").split("\n").slice(0, 8).join("\n").slice(0, 1000),
    page: where().slice(0, 300),
    build: import.meta.env.MODE === "production" ? (document.querySelector('script[type="module"][src*="/assets/"]')?.getAttribute("src") ?? "") : "dev",
    at: new Date().toISOString(),
  });
  try { navigator.sendBeacon?.(URL_PATH, new Blob([body], { type: "application/json" })); } catch { /* no beacon: dropped */ }
  return id;
}

let installed = false;
/** Listen for errors nothing else caught. Chunk failures are left to main.tsx (it reloads once). */
export function installErrorReports() {
  if (installed || typeof window === "undefined") return;
  installed = true;
  addEventListener("error", (e) => {
    if (!e.error || isChunkError(e.error)) return; // a resource that failed to load (no error object), or a chunk
    reportError(e.error, "error");
  });
  addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    if (isChunkError(r) || (r && typeof r === "object" && (r as { name?: string }).name === "AbortError")) return;
    reportError(r, "rejection");
  });
}
