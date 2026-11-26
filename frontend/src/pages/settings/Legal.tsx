/** Settings: the legal side of an organisation's use of Welcome Wave.
 *
 *  OrgLegal (Settings -> Organisation, admins): the Data Processing Agreement (who accepted it for the organisation, when,
 *  which version; accept it, with one explicit confirmation; download the record), every document, and the details of the
 *  company that runs this server as the documents show them. A detail that is not set is said to be not published, with
 *  one quiet line on what the documents say meanwhile and who can set it: never a made-up name.
 *
 *  YourDocuments (Settings -> Account, everyone): the documents, and when this person accepted each of theirs.
 *
 *  GET /api/org/legal, POST /api/org/legal/dpa, GET /api/org/legal/dpa/record, GET /api/legal/status. The documents open
 *  on the site in a new tab (src/legal/paths.ts); their text is not downloaded here. */
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, day, download, errorMessage, post } from "../../api";
import { Check } from "../../consent/Check";
import { useFocusOnClose } from "../../consent/focus";
import { loadLegalStatus, useLegalStatus } from "../../consent/legalStatus";
import "../../consent/consent.css";
import { LEGAL_PAGES, LEGAL_PATH, versionDate } from "../../legal/paths";
import { FALLBACKS } from "../../legal/tokens";
import type { LegalDocId, LegalToken, VersionedDocId } from "../../legal/types";
import { useSession } from "../../session";
import { Skeleton } from "../../fx";
import { Msg, Note, toast } from "../../ui";
import { SectionCard } from "./kit";

type Person = { id: string; name: string | null; email: string | null };
type Acceptance = { version: string; accepted_at: string | null; accepted_by: Person | null };
type OrgLegalData = {
  dpa: { version: string; accepted: boolean; accepted_at: string | null; accepted_by: Person | null; previous: Acceptance[] };
  versions: Record<VersionedDocId, string>;
  operator: Record<LegalToken, string | null>;
};

const VERSION_OF: Record<LegalDocId, VersionedDocId> = {
  terms: "terms", privacy: "privacy", dpa: "dpa", recording: "recording", subprocessors: "subprocessors", acceptable_use: "terms",
};
const who = (p: Person | null) => (!p ? "an admin" : p.name && p.email ? `${p.name} (${p.email})` : p.name || p.email || "a former member");
const NewTab = () => <span className="sr-only"> (opens in a new tab)</span>;

/** What the operator's details are called here, and in the hint when one is missing. */
const DETAILS: { key: LegalToken; label: string; missing: string }[] = [
  { key: "operator", label: "Company", missing: "legal name" },
  { key: "address", label: "Registered address", missing: "address" },
  { key: "contact_email", label: "Legal notices and questions", missing: "contact address" },
  { key: "privacy_email", label: "Privacy requests", missing: "privacy address" },
  { key: "governing_law", label: "Governing law", missing: "governing law" },
  { key: "courts", label: "Courts", missing: "courts" },
];

export function OrgLegal() {
  const { me } = useSession();
  const org = me.org_name;
  const [data, setData] = useState<OrgLegalData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [sure, setSure] = useState(false);
  const [missed, setMissed] = useState(0);
  const [busy, setBusy] = useState<"accept" | "record" | null>(null);
  const [msg, setMsg] = useState<Msg>(null);

  const load = useCallback(() => {
    setError(null);
    api<OrgLegalData>("/org/legal").then(setData).catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(load, [load]);

  async function accept(e: FormEvent) {
    e.preventDefault();
    if (!data || busy) return;
    if (!sure) return setMissed((n) => n + 1);
    setBusy("accept"); setMsg(null);
    try {
      setData(await post<OrgLegalData>("/org/legal/dpa", { version: data.dpa.version }));
      setConfirming(false); setSure(false); setMissed(0);
      toast("Data Processing Agreement accepted");
    } catch (err) {
      if (err instanceof ApiError && err.code === "version_changed") { load(); setMsg({ ok: false, text: "The agreement changed since this page loaded. Read the current version, then accept it." }); }
      else setMsg({ ok: false, text: errorMessage(err) });
    } finally { setBusy(null); }
  }
  async function record() {
    setBusy("record"); setMsg(null);
    try { await download("/org/legal/dpa/record", "welcome-wave-dpa-acceptance.txt"); }
    catch (err) { setMsg({ ok: false, text: errorMessage(err) }); }
    finally { setBusy(null); }
  }
  const cancel = () => { setConfirming(false); setSure(false); setMissed(0); setMsg(null); };
  // the confirmation closes: focus goes back to "Accept for …", or, once accepted, to the line that says who accepted it
  useFocusOnClose(confirming, () => [document.querySelector(".lgc-dpa [data-dpa-return]")]);

  const dpa = data?.dpa;
  // the privacy address falls back to the contact address, as the documents do (src/legal/tokens.ts `detail`)
  const value = (key: LegalToken) => data?.operator[key] ?? (key === "privacy_email" ? data?.operator.contact_email ?? null : null);
  const missing = data ? DETAILS.filter((d) => !value(d.key)) : [];

  return (
    <SectionCard id="org-legal" title="Legal" description="The agreement about your organisation's data, the documents, and who runs this service.">
      {error && <div className="set-inline-error"><p className="error" role="alert">{error}</p><button type="button" className="secondary" onClick={load}>Try again</button></div>}
      {!data && !error && <Skeleton lines={4} height={16} />}
      {data && dpa && (
        <div className="lgc-set">
          <div className="ww-panel lgc-dpa" aria-labelledby="lgc-dpa-h">
            <div className="lgc-dpa-row">
              <div>
                <p className="lgc-dpa-title" id="lgc-dpa-h">Data Processing Agreement</p>
                {dpa.accepted ? (
                  <p className="lgc-dpa-text" data-dpa-return>
                    Accepted for <strong>{org}</strong> by <strong>{who(dpa.accepted_by)}</strong>{dpa.accepted_at ? ` on ${day(dpa.accepted_at)}` : ""}.
                    Version of {versionDate(dpa.version)}.
                  </p>
                ) : (
                  <p className="lgc-dpa-text">
                    How Welcome Wave handles personal data on <strong>{org}</strong>'s behalf. An admin accepts it once, for the whole
                    organisation. Version of {versionDate(dpa.version)}.
                  </p>
                )}
              </div>
              <span className={`lgc-state${dpa.accepted ? " is-done" : ""}`}>{dpa.accepted ? "Accepted" : "Not accepted yet"}</span>
            </div>

            {!confirming && (
              <div className="lgc-dpa-actions">
                {!dpa.accepted && <button type="button" className="set-primary" data-dpa-return onClick={() => { setConfirming(true); setMsg(null); }}>Accept for {org}</button>}
                <a className="set-link-btn" href={LEGAL_PATH.dpa} target="_blank" rel="noopener">Read the agreement<NewTab /></a>
                {dpa.accepted && (
                  <>
                    <button type="button" className="secondary" onClick={record} disabled={busy !== null}>
                      {busy === "record" ? "Preparing…" : "Download the record"}
                    </button>
                    {/* the record names a version, not its words: this opens the agreement with the print dialog, to keep both */}
                    <a className="set-link-btn" href={`${LEGAL_PATH.dpa}?print=1`} target="_blank" rel="noopener">Save the agreement as PDF<NewTab /></a>
                  </>
                )}
              </div>
            )}
            {confirming && (
              <form className="lgc-dpa-confirm" onSubmit={accept} noValidate onKeyDown={(e) => { if (e.key === "Escape") cancel(); }}>
                <Check id="lgc-dpa-sure" checked={sure} onChange={setSure} missed={missed} disabled={busy !== null} autoFocus>
                  I have read the <a href={LEGAL_PATH.dpa} target="_blank" rel="noopener">Data Processing Agreement<NewTab /></a> and
                  may accept it for {org}.
                </Check>
                <div className="lgc-dpa-actions">
                  <button type="button" className="secondary" onClick={cancel} disabled={busy !== null}>Cancel</button>
                  <button type="submit" className="set-primary" disabled={busy !== null}>
                    {busy === "accept" ? <><span className="en-spin" aria-hidden="true" />Accepting…</> : "Accept the agreement"}
                  </button>
                </div>
              </form>
            )}
            <Note msg={msg} />
            {dpa.previous.length > 0 && (
              <ul className="lgc-dpa-earlier" aria-label="Earlier versions accepted">
                {dpa.previous.map((p) => (
                  <li key={p.version}>Version of {versionDate(p.version)}: accepted by {who(p.accepted_by)}{p.accepted_at ? ` on ${day(p.accepted_at)}` : ""}.</li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <h3>Documents</h3>
            <ul className="lgc-doclist">
              {LEGAL_PAGES.map((p) => (
                <li key={p.id}>
                  <a href={p.path} target="_blank" rel="noopener">{p.title}<NewTab /></a>
                  <span>Version of {versionDate(data.versions[VERSION_OF[p.id]])}</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3>Who runs this service</h3>
            <dl className="lgc-operator">
              {DETAILS.map((d) => {
                const v = value(d.key);
                return (
                  <div key={d.key}>
                    <dt>{d.label}</dt>
                    {v ? <dd>{d.key.endsWith("_email") ? <a href={`mailto:${v}`}>{v}</a> : v}</dd> : <dd className="is-unset">Not published</dd>}
                  </div>
                );
              })}
            </dl>
            {missing.length > 0 && (
              <p className="lgc-hint">
                Until {missing.length === 1 ? "this is" : "these are"} set, the documents say “{FALLBACKS[missing[0].key]}”
                {missing.length === 1 ? "" : " and the like"} instead of the {list(missing.map((d) => d.missing))}. Whoever runs this
                server sets {missing.length === 1 ? "it" : "them"}: the <code>LEGAL_*</code> settings.
              </p>
            )}
          </div>
        </div>
      )}
    </SectionCard>
  );
}

const list = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** Settings -> Account: every document, and when this person accepted each of the ones a person accepts. */
export function YourDocuments() {
  const { me, config } = useSession();
  const status = useLegalStatus();
  useEffect(() => { if (!status) loadLegalStatus(me.id).catch(() => { /* the list still links every document */ }); }, [status, me.id]);
  const versions = status?.versions ?? config.legal?.versions ?? {};
  const mine = (id: LegalDocId) => {
    if (!status || !["terms", "privacy", "recording"].includes(id)) return null;
    const a = status.accepted.find((x) => x.document === id && x.version === status.versions[id as VersionedDocId]);
    if (a) return `${id === "recording" ? "You acknowledged it" : "You accepted it"}${a.at ? ` on ${day(a.at)}` : ""}`;
    return id === "recording" ? "Not acknowledged yet" : "Not accepted yet";
  };
  return (
    <SectionCard id="account-legal" title="Terms and privacy" description="The documents that apply to your use of Welcome Wave.">
      <ul className="lgc-doclist">
        {LEGAL_PAGES.map((p) => {
          const v = versions[VERSION_OF[p.id]], yours = mine(p.id);
          return (
            <li key={p.id}>
              <a href={p.path} target="_blank" rel="noopener">{p.title}<NewTab /></a>
              <span>{yours && <span className="lgc-yours">{yours}. </span>}{v ? `Version of ${versionDate(v)}` : ""}</span>
            </li>
          );
        })}
      </ul>
    </SectionCard>
  );
}
