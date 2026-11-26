/** The workspace router (Workspace's useHash) follows location.hash. history.replaceState changes the URL without a
 *  hashchange event, so a page that rewrote its own query (?stop=, ?presenter=, ?simulate=) left the router on the old
 *  URL: tapping the page's own link then changed nothing it could see. replaceRoute rewrites the URL without a history
 *  entry and tells the router, so its state always matches the address bar. */
export const ROUTE_EVENT = "ww:route";

export function replaceRoute(hash: string) {
  if (location.hash === hash) return;
  history.replaceState(history.state, "", `${location.pathname}${location.search}${hash}`);
  dispatchEvent(new Event(ROUTE_EVENT));
}
