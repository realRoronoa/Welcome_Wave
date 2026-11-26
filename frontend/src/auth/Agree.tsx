/** The agreement every screen that makes an account asks for: creating a login, creating an organisation, joining one by
 *  invitation. One explicit checkbox, never ticked for the person, with the two documents one click away in a new tab (so
 *  what was typed in the form stays). The screens send `accept: JOIN_DOCUMENTS` with the request that creates the account,
 *  and the API records it in that same request: without it nothing is created (backend/app/legal.py `require_on_joining`).
 *
 *  Not a native `required`: "Continue with Google" and an invitation's "Join" are not form submissions, so each screen
 *  checks `agreed` itself, and a missed box says so here, next to the box, and takes focus. */
import { useEffect, useRef } from "react";
import { LEGAL_PATH } from "../legal/paths";

/** What a person accepts by making an account. The recording notice is acknowledged later, where recordings are; the
 *  data-processing agreement is the organisation's, accepted by an admin in Settings. */
export const JOIN_DOCUMENTS = ["terms", "privacy"] as const;

type Props = {
  id: string; checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean;
  /** The person tried to continue without it: show the message and move focus to the box. Counts tries, so a second
   *  try moves focus again. */
  missed?: number;
};

export function Agree({ id, checked, onChange, disabled, missed = 0 }: Props) {
  const box = useRef<HTMLInputElement>(null);
  const invalid = missed > 0 && !checked;
  useEffect(() => { if (missed > 0) box.current?.focus(); }, [missed]);
  return (
    <div className={`en-agree${invalid ? " is-missed" : ""}`}>
      <input ref={box} id={id} type="checkbox" checked={checked} disabled={disabled} aria-required="true" aria-invalid={invalid || undefined}
             aria-describedby={invalid ? `${id}-msg` : undefined} onChange={(e) => onChange(e.target.checked)} />
      <label htmlFor={id}>
        I agree to the <DocLink to="terms">Terms</DocLink> and have read the <DocLink to="privacy">Privacy Policy</DocLink>.
      </label>
      {invalid && <p id={`${id}-msg`} className="en-agree-msg" role="alert">Tick the box to continue.</p>}
    </div>
  );
}

/** A legal document, opened in a new tab: the form behind it keeps what was typed. */
export function DocLink({ to, children }: { to: keyof typeof LEGAL_PATH; children: string }) {
  return (
    <a href={LEGAL_PATH[to]} target="_blank" rel="noopener">{children}<span className="sr-only"> (opens in a new tab)</span></a>
  );
}
