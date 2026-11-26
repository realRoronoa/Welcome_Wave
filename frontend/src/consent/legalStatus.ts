/** What the signed-in person has accepted of the legal documents, and what they still need to (GET /api/legal/status).
 *  One copy for the whole app: the gate before the workspace (LegalGate.tsx), the recording notice on the KT pages
 *  (RecordingNotice.tsx) and Settings all read it here, so an acceptance made in one place is known in the others at once.
 *
 *  Accepting sends the versions this build shows (`versions`), so the API refuses (409 `version_changed`) an acceptance of
 *  a document that changed after the page loaded: nobody accepts a version they were not shown. */
import { useEffect, useState } from "react";
import { api, post } from "../api";
import type { VersionedDocId } from "../legal/types";

export type PersonalDoc = "terms" | "privacy" | "recording";
export type Accepted = { document: PersonalDoc; version: string; at: string | null };
export type LegalStatus = {
  /** The documents whose current version this person has not accepted. */
  needs: PersonalDoc[];
  /** Everything they have accepted, newest first. */
  accepted: Accepted[];
  versions: Record<VersionedDocId, string>;
  /** The ones nobody may skip: the terms and the privacy policy. */
  required: PersonalDoc[];
  /** LEGAL_ENFORCE is on: the API itself refuses the workspace until `required` is accepted. */
  enforced: boolean;
};

let current: LegalStatus | null = null;
let owner: string | null = null; // whose status `current` is: another person signing in on this page starts clean
let pending: Promise<LegalStatus> | null = null;
const listeners = new Set<() => void>();

function set(status: LegalStatus | null) {
  current = status;
  listeners.forEach((fn) => fn());
}

/** Read the status from the API (again, if `force`). Rejects with the API's error. */
export function loadLegalStatus(userId: string, force = false): Promise<LegalStatus> {
  if (owner !== userId) { owner = userId; pending = null; set(null); }
  if (current && !force) return Promise.resolve(current);
  const mine = userId;
  pending ??= api<LegalStatus>("/legal/status")
    .then((s) => { if (owner === mine) set(s); return s; })
    .finally(() => { pending = null; });
  return pending;
}

/** Record the person's acceptance of the current version of each document. Resolves with the new status. */
export async function acceptDocuments(documents: PersonalDoc[], versions: Partial<Record<VersionedDocId, string>>): Promise<LegalStatus> {
  const shown = Object.fromEntries(documents.filter((d) => versions[d]).map((d) => [d, versions[d]]));
  const mine = owner;
  const s = await post<LegalStatus & { recorded: string[] }>("/legal/accept", { documents, versions: shown });
  if (owner === mine) set(s);
  return s;
}

/** Signed out: the next person's status is their own. */
export function forgetLegalStatus() {
  owner = null; pending = null;
  set(null);
}

/** The status as last read, re-rendering when it changes; null until LegalGate has loaded it. */
export function useLegalStatus(): LegalStatus | null {
  const [, tick] = useState(0);
  useEffect(() => {
    const on = () => tick((n) => n + 1);
    listeners.add(on);
    return () => { listeners.delete(on); };
  }, []);
  return current;
}
