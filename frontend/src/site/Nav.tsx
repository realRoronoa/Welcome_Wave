/* The site's header: the mark and name, a folio naming the chapter in view (wide screens), the four links, and the way in.
   Transparent over the cover, solid paper with a hairline once the page scrolls, inverted over the black finale and footer.
   Under 900 px the links move into a full-screen ink menu (a modal dialog: focus trapped, the page behind inert and still). */
import { CSSProperties, MouseEvent, useEffect, useRef, useState } from "react";
import { Logo } from "../ui";
import { seemsSignedIn } from "../surface";
import { jumpTo, prefetch, saveScroll, toApp, toSite } from "./handover";

type Folio = { n: string; title: string; time: string } | null;

export default function Nav({ path }: { path: string }) {
  const home = path === "/";
  const [scrolled, setScrolled] = useState(false);
  const [spy, setSpy] = useState<string>(home ? "home" : "");
  const [folio, setFolio] = useState<Folio>(null);
  const [open, setOpen] = useState(false);
  const signedIn = seemsSignedIn();
  const sentinel = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const sheet = useRef<HTMLDivElement>(null);
  /** Where focus goes when the menu closes: back to Menu (Esc, Close), or nowhere because a link took it (the page changes),
   *  or into the section an in-page link named, once the page behind is focusable again. */
  const after = useRef<{ kind: "menu" } | { kind: "none" } | { kind: "jump"; hash: string }>({ kind: "menu" });

  // solid once the page has moved 8 px (an observer on a marker, not a scroll listener)
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setScrolled(!e.isIntersecting));
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // which link is current, and the folio: whatever section crosses a line 45 % down the viewport
  useEffect(() => {
    setSpy(home ? "home" : ""); setFolio(null);
    const sections = document.querySelectorAll<HTMLElement>(".site [data-nav]");
    if (!sections.length) return;
    // the sections on the line right now (a jump can leave none: the finale and footer name no link, so nothing is current)
    const live = new Set<Element>();
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) live.add(e.target); else live.delete(e.target);
      }
      const d = [...sections].filter((s) => live.has(s)).pop()?.dataset;
      setSpy(d?.nav ?? "");
      setFolio(d?.chapter ? { n: d.chapter, title: d.title ?? "", time: d.time ?? "" } : null);
    }, { rootMargin: "-45% 0px -54% 0px" });
    sections.forEach((s) => io.observe(s));
    return () => io.disconnect();
  }, [path, home]);

  // the menu is modal: the page behind is inert and does not scroll; Esc closes; focus goes in, and back to Menu after
  useEffect(() => {
    if (!open) return;
    const behind = [bar.current, document.getElementById("content"), document.querySelector<HTMLElement>(".s-footer")];
    behind.forEach((el) => el && (el.inert = true));
    const root = document.documentElement, overflow = root.style.overflow;
    root.style.overflow = "hidden";
    sheet.current?.querySelector<HTMLElement>("a, button")?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); setOpen(false); return; }
      if (e.key !== "Tab" || !sheet.current) return;
      const items = [...sheet.current.querySelectorAll<HTMLElement>("a, button")];
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      root.style.overflow = overflow;
      behind.forEach((el) => el && (el.inert = false));
      const next = after.current;
      after.current = { kind: "menu" };
      if (next.kind === "jump") jumpTo(next.hash, false); // scroll there and focus its heading
      else if (next.kind === "menu") menuButton.current?.focus();
    };
  }, [open]);
  useEffect(() => setOpen(false), [path]);

  const anchor = (id: string) => (home ? `#${id}` : `/#${id}`);
  const links = [
    { href: "/", label: "Home", key: "home" },
    { href: anchor("product"), label: "Product", key: "product" },
    { href: anchor("security"), label: "Security", key: "security" },
    { href: "/about", label: "About", key: "about" },
  ];
  const current = (key: string) => (key === "home" ? home : key === "about" ? path === "/about" : false);
  const active = (key: string) => (home ? spy === key : path === "/about" && key === "about");
  const site = (e: MouseEvent<HTMLAnchorElement>, href: string) => {
    const plain = e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
    // from the open menu, an anchor on this page waits for the menu to close (the page behind is inert and locked until then);
    // the menu's cleanup then scrolls there and puts focus on the section's heading
    const url = new URL(href, location.href);
    if (open && plain && url.pathname === location.pathname) {
      e.preventDefault();
      const to = url.pathname + url.hash;
      if (to !== location.pathname + location.hash) { saveScroll(); history.pushState(null, "", to); }
      after.current = { kind: "jump", hash: url.hash || "#top" }; // "Home" on the home page: the top
      setOpen(false);
      return;
    }
    if (open && plain) after.current = { kind: "none" }; // another page: it moves focus to its own heading
    toSite(e, href);
    setOpen(false);
  };

  const way = (inMenu: boolean) => signedIn ? (
    <a className="s-btn s-btn-ink s-btn-sm" href="/app" onClick={(e) => toApp(e, "/app")} {...prefetch}>Open Welcome Wave</a>
  ) : (
    <>
      <a className={inMenu ? "s-btn s-btn-line s-btn-sm" : "s-navlogin"} href="/login" onClick={(e) => toApp(e, "/login")} {...prefetch}>Log in</a>
      <a className="s-btn s-btn-ink s-btn-sm" href="/signup" onClick={(e) => toApp(e, "/signup")} {...prefetch}>Get started</a>
    </>
  );

  return (
    <>
      <div ref={sentinel} className="s-sentinel" aria-hidden="true" />
      <header ref={bar} className={`s-nav${scrolled ? " is-solid" : ""}`}>
        <nav className="s-nav-in" aria-label="Main">
          <div className="s-nav-start">
            <a className="s-brand" href="/" onClick={(e) => site(e, "/")} aria-label="Welcome Wave home page">
              <Logo size={24} /><span>Welcome Wave</span>
            </a>
            {folio && (
              <p className="s-folio" aria-hidden="true">
                <span>{folio.n}</span><span className="s-folio-title"> · {folio.title}</span>{folio.time && <> · <span className="s-tnum">{folio.time}</span></>}
              </p>
            )}
          </div>
          <ul className="s-links">
            {links.map((l) => (
              <li key={l.key}>
                <a href={l.href} onClick={(e) => site(e, l.href)} aria-current={current(l.key) ? "page" : undefined}
                   className={active(l.key) ? "is-active" : undefined}>{l.label}</a>
              </li>
            ))}
          </ul>
          <div className="s-nav-end">
            {way(false)}
            <button ref={menuButton} type="button" className="s-menu-btn" aria-expanded={open} aria-controls={open ? "s-menu" : undefined} onClick={() => setOpen(true)}>
              <span className="s-menu-icon" aria-hidden="true" /><span>Menu</span>
            </button>
          </div>
        </nav>
      </header>
      {open && (
        <div ref={sheet} id="s-menu" className="s-menu" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="s-menu-top">
            <a className="s-brand" href="/" onClick={(e) => site(e, "/")} aria-label="Welcome Wave home page">
              <Logo size={24} /><span>Welcome Wave</span>
            </a>
            <button type="button" className="s-menu-btn" onClick={() => setOpen(false)}>
              <span className="s-menu-icon is-x" aria-hidden="true" /><span>Close</span>
            </button>
          </div>
          <ol className="s-menu-links">
            {links.map((l, i) => (
              <li key={l.key} style={{ "--i": i } as CSSProperties}>
                <a href={l.href} onClick={(e) => site(e, l.href)} aria-current={current(l.key) ? "page" : undefined}>
                  <span className="s-menu-n" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>{l.label}
                </a>
              </li>
            ))}
          </ol>
          <div className="s-menu-way">{way(true)}</div>
        </div>
      )}
    </>
  );
}
