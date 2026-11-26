/** Keyboard focus when a small inline step closes (a confirmation, a note, a request). The step took focus when it opened;
 *  when it goes, the button that opened it is often gone too (re-rendered, or replaced by the result), and focus would
 *  fall to <body>: a keyboard or screen-reader user loses their place. This puts it back. */
import { useEffect, useRef } from "react";

const TAKES_FOCUS = "a[href], button, input, select, textarea, [tabindex]";

/** Focus `el` without scrolling. Something that does not take focus by itself (a status line, a heading) gets
 *  tabindex="-1": reachable by script, never a Tab stop. False when there is nothing to focus or it is disabled. */
export function focusOn(el: Element | null | undefined): boolean {
  if (!(el instanceof HTMLElement) || !el.isConnected || (el as HTMLButtonElement).disabled) return false;
  if (!el.matches(TAKES_FOCUS)) el.tabIndex = -1;
  el.focus({ preventScroll: true });
  return document.activeElement === el;
}

/** When `open` turns false, focus the first of `find()`'s answers that can take it: the opener if it is back, otherwise
 *  the line that says what happened. `find` runs after the closing render, so it sees the page as it now is. */
export function useFocusOnClose(open: boolean, find: () => (Element | null | undefined)[]) {
  const was = useRef(open);
  const finder = useRef(find);
  finder.current = find;
  useEffect(() => {
    if (was.current && !open) finder.current().some(focusOn);
    was.current = open;
  }, [open]);
}
