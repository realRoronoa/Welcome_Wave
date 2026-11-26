/** Shared reads for the lifecycle pages (kit.tsx's useApi, hires.ts, the demo clock).
 *
 *  Several parts of one screen read the same resources (the project's members, each hire's program), and a live update
 *  makes them all look again at once. Reads are shared: a GET already in flight is joined, and one answered in the last
 *  few seconds is reused when a part first mounts. A reload after a change (live updates, a write) always asks again,
 *  and any write through useAction forgets what was kept. Signing in or out forgets everything (resetShared). */
import { api } from "../api";

const inflight = new Map<string, Promise<unknown>>();
const kept = new Map<string, { at: number; data: unknown }>();
export const KEEP_MS = 4000;
let generation = 0; // bumped when the account changes: a read started for the previous person is never kept or joined

export function getShared<T>(path: string, maxAge = KEEP_MS): Promise<T> {
  const k = kept.get(path);
  if (maxAge > 0 && k && Date.now() - k.at < maxAge) return Promise.resolve(k.data as T);
  const f = inflight.get(path);
  if (f) return f as Promise<T>;
  const gen = generation;
  const p = api<T>(path).then((d) => { if (gen === generation) kept.set(path, { at: Date.now(), data: d }); return d; })
    .finally(() => { if (inflight.get(path) === p) inflight.delete(path); });
  inflight.set(path, p);
  return p;
}
/** Forget every kept read (after a write, or when the demo clock moves). */
export function forgetShared() { kept.clear(); }
/** Forget everything read for the signed-in person, reads in flight included: on sign-in and sign-out, so the next
 *  account (on a shared machine, or "Switch account" in the demo) never sees the previous one's data. */
export function resetShared() { generation++; kept.clear(); inflight.clear(); }

