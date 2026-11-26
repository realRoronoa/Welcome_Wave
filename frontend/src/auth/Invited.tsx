import { useState } from "react";
import { ApiError, day, errorMessage, Me, post, ROLE_LABEL } from "../api";
import { Msg, Note } from "../ui";
import type { Fb } from ".";
import { Agree, JOIN_DOCUMENTS } from "./Agree";
import { Busy, EntryLayout, Identity, initials } from "./Entry";

export type PendingInvitation = {
  org_id: string; org_name: string; role: string; invited_by_name: string | null; invited_by_email: string | null;
  org_created_at: string | null; projects: string[];
};

type Props = {
  fb: Fb; invitations: PendingInvitation[]; signupEnabled: boolean;
  onJoined: (me: Me) => void; onExpired: (orgId: string) => void; onCreateOwn: () => void; retry: () => void; signOut: () => void;
};

// the email is what the person can check: a display name alone is easy to imitate
const inviter = (inv: PendingInvitation) =>
  inv.invited_by_name && inv.invited_by_email ? `${inv.invited_by_name} (${inv.invited_by_email})` : inv.invited_by_email || inv.invited_by_name;

/** 403 invitation_pending: the login was invited somewhere. Joining is an explicit choice, never automatic, and it carries
 *  the person's agreement to the terms and the privacy policy (one box above the invitations, Agree.tsx): the API records
 *  it with the account, and without it nobody joins. */
export default function Invited({ fb, invitations, signupEnabled, onJoined, onExpired, onCreateOwn, retry, signOut }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [agreed, setAgreed] = useState(false);
  const [missed, setMissed] = useState(0);

  async function join(inv: PendingInvitation) {
    if (!agreed) { setMsg(null); setMissed((n) => n + 1); return; }
    setBusy(inv.org_id); setMsg(null);
    try {
      onJoined(await post<Me>("/invitations/accept", { org_id: inv.org_id, accept: JOIN_DOCUMENTS }));
    } catch (e) {
      setBusy(null);
      // 409: the account changed meanwhile (joined in another tab, invitations replaced): ask the server again
      if (e instanceof ApiError && (e.code === "email_not_verified" || e.status === 409)) return retry();
      if (e instanceof ApiError && e.status === 404) {
        onExpired(inv.org_id);
        return setMsg({ ok: false, text: `The invitation to ${inv.org_name} has expired or was withdrawn. Ask ${inviter(inv) || "the person who invited you"} to invite you again.` });
      }
      setMsg({ ok: false, text: errorMessage(e) });
    }
  }

  return (
    <EntryLayout wide title={invitations.length === 1 ? "You have been invited" : `You have ${invitations.length} invitations`}
                 lead="Choose the team to join.">
      <p className="en-callout shield">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>
        <span>Only join a team you recognise: check who invited you.</span>
      </p>
      <Agree id="inv-agree" checked={agreed} onChange={setAgreed} disabled={busy !== null} missed={missed} />
      <ul className="en-invites">
        {invitations.map((inv, i) => (
          <li key={inv.org_id} className="en-invite spot" style={{ animationDelay: `${i * 70}ms` }}>
            <div className="en-invite-head">
              <span className="en-org-tile" aria-hidden="true">{initials(inv.org_name)}</span>
              <div className="en-invite-title">
                <h2>{inv.org_name}</h2>
                <span className="en-role">{ROLE_LABEL[inv.role] ?? inv.role}</span>
              </div>
            </div>
            <dl className="en-facts">
              {inviter(inv) && (
                <div><dt>Invited by</dt><dd>
                  {inv.invited_by_name && <span>{inv.invited_by_name}</span>}
                  {inv.invited_by_email && <code className="en-email">{inv.invited_by_email}</code>}
                </dd></div>
              )}
              {inv.org_created_at && (
                <div><dt>Organisation created</dt><dd>
                  {day(inv.org_created_at)}
                  {recent(inv.org_created_at) && <span className="en-recent">New this week</span>}
                </dd></div>
              )}
              {inv.projects.length > 0 && (
                <div><dt>{inv.projects.length === 1 ? "Project" : "Projects"}</dt><dd className="en-chips">
                  {inv.projects.map((p) => <span key={p} className="en-chip-light">{p}</span>)}
                </dd></div>
              )}
            </dl>
            <button className="en-primary" onClick={() => join(inv)} disabled={busy !== null}>
              <Busy busy={busy === inv.org_id} label="Joining…">Join {inv.org_name}</Busy>
            </button>
          </li>
        ))}
      </ul>
      <Note msg={msg} />
      {signupEnabled && (
        <p className="en-alt">Not the team you expected?{" "}
          <button type="button" className="en-link" onClick={onCreateOwn}>Create my own organisation instead</button></p>
      )}
      <Identity email={fb.email()} onSwitch={signOut} />
    </EntryLayout>
  );
}

// a lookalike organisation is usually brand new: say so next to the date
const recent = (iso: string) => Date.now() - new Date(iso).getTime() < 7 * 864e5;
