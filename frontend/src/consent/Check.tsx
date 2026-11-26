/** One explicit confirmation: a checkbox, never ticked for the person, beside the sentence they are confirming. A missed
 *  box says so next to itself, and the first missed box of the form takes focus (as the sign-up agreement does,
 *  auth/Agree.tsx). */
import { ReactNode, useEffect, useRef } from "react";

type Props = { id: string; checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean; missed?: number; autoFocus?: boolean; children: ReactNode };

export function Check({ id, checked, onChange, disabled, missed = 0, autoFocus, children }: Props) {
  const box = useRef<HTMLInputElement>(null);
  const invalid = missed > 0 && !checked;
  // a miss takes focus to the first unticked box of the form: with two boxes, each runs this, and only the first one moves
  useEffect(() => {
    const el = box.current;
    if (missed <= 0 || !el || el.checked) return;
    const first = (el.closest("form") ?? document).querySelector<HTMLInputElement>(".lgc-check input[type=checkbox]:not(:checked)");
    if (!first || first === el) el.focus();
  }, [missed]);
  return (
    <div className={`lgc-check${invalid ? " is-missed" : ""}`}>
      <input ref={box} id={id} type="checkbox" checked={checked} disabled={disabled} autoFocus={autoFocus} aria-required="true"
             aria-invalid={invalid || undefined} aria-describedby={invalid ? `${id}-msg` : undefined} onChange={(e) => onChange(e.target.checked)} />
      <label htmlFor={id}>{children}</label>
      {invalid && <p id={`${id}-msg`} className="lgc-check-msg" role="alert">Tick the box to continue.</p>}
    </div>
  );
}
