/** A quiet line at the top of the workspace for an admin, until the organisation has accepted the Data Processing
 *  Agreement. New organisations accept terms and privacy at sign-up; the DPA is a separate, organisation-wide step in
 *  Settings > Organisation > Legal, and nothing else would tell an admin it is waiting. Not a gate: nothing is blocked.
 *  It goes by itself once accepted, and a person can dismiss it for the rest of the visit (it comes back next visit). */
import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon } from "../ui";
import { useSession } from "../session";

let dismissed = false;

export default function DpaPrompt() {
  const { me } = useSession();
  const [pending, setPending] = useState(false);
  const [hidden, setHidden] = useState(dismissed);
  useEffect(() => {
    if (me.role !== "admin") return;
    let live = true;
    api<{ dpa: { accepted: boolean } }>("/org/legal").then((d) => { if (live) setPending(!d.dpa.accepted); }).catch(() => { /* a quiet reminder: no reminder when unsure */ });
    return () => { live = false; };
  }, [me.id, me.role, me.org_name]);
  // Settings > Organisation > Legal accepts it: this line leaves when the hash lands there, and is read again on return
  useEffect(() => {
    if (me.role !== "admin") return;
    const on = () => api<{ dpa: { accepted: boolean } }>("/org/legal").then((d) => setPending(!d.dpa.accepted)).catch(() => {});
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, [me.role]);
  if (me.role !== "admin" || !pending || hidden) return null;
  return (
    <div className="ai-off dpa-prompt" role="note">
      <Icon name="alert" size={16} />
      <p><strong>One step left for {me.org_name}.</strong> An admin has to accept the Data Processing Agreement, once, for the whole organisation.{" "}
        <a href="#/settings?tab=organisation">Review and accept it in Settings</a></p>
      <button type="button" className="icon" aria-label="Hide this reminder for now" onClick={() => { dismissed = true; setHidden(true); }}><Icon name="close" size={14} /></button>
    </div>
  );
}
