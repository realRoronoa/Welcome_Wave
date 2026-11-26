/* The landing page's film: one continuous story, placed by the page's own (native) scroll; the footage plays at its natural
   speed while it is on screen.
   The cover ("Learn the codebase from the people who built it.") over the office; the scroll leans in on the new engineer,
   and the story runs on with it:
     1 The first day       at his desk; close on his face
     2 The struggle        he gets up and walks away from the desk
     3 The mentor          a senior notices, comes over and explains; the camera pushes into the monitor...
     4 Knowledge           ...and the product grows out of its screen: panels in a dark 3D space, teal threads from the
                           recorded explanation to the code it covers
     5 First contribution  the camera travels on to his journey, from learning path to approved pull request
     6 Business impact     the team and the leadership report; the camera's last stop, with the way in
   Layout: one sticky view (a screen tall) holds the footage (CinematicStage), the words (the cover and the six captions,
   in order, first in the DOM, the cover over its shade), the product world (World), a quiet chapter rail and the scroll
   cue. Under it, the flow holds one spacer per section, as long as its stretch of film: the scroll's length, the in-page
   anchors and the nav's folio. ScrollStoryController.ts places the film where the page is. */
import { CSSProperties, ReactNode, useEffect, useRef } from "react";
import { prefetch, toApp } from "../handover";
import { Words } from "../parts";
import { CinematicStage } from "./CinematicStage";
import { FILM_END, SECTIONS } from "./film";
import { runStory } from "./ScrollStoryController";
import { World } from "./World";
import "./cinematic.css";

type Caption = { id: string; h: string; p?: string; layout: "foot-l" | "foot-r" | "top" | "stop" };

const CAPTIONS: Caption[] = [
  { id: "product", layout: "foot-l", h: "Your first day shouldn’t feel like your hundredth unanswered question.",
    p: "Every new engineer deserves more than a repository link and a meeting invitation." },
  { id: "struggle", layout: "foot-l", h: "The knowledge exists. Finding it shouldn’t depend on who happens to be online." },
  { id: "mentor", layout: "foot-r", h: "What if the right explanation was already waiting for you?" },
  { id: "knowledge", layout: "top", h: "Capture once. Connect to code. Reuse everywhere." },
  { id: "contribution", layout: "top", h: "From the first question to the first contribution." },
  { id: "impact", layout: "stop", h: "Better onboarding. Stronger engineering teams." },
];

export default function CinematicLandingPage({ cover, signedIn }: { cover: ReactNode; signedIn: boolean }) {
  const track = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const world = useRef<HTMLDivElement>(null);
  const words = useRef<HTMLDivElement>(null);

  useEffect(() => runStory({ track: track.current!, stage: stage.current!, world: world.current!, words: words.current! }), []);

  const live = signedIn ? "/app" : "/login";
  return (
    <div ref={track} className="s-film">
      <div className="s-view">
        <CinematicStage ref={stage} />
        <div ref={words} className="s-words">
          <div className="s-cover-shade" aria-hidden="true" />
          {cover}
          {CAPTIONS.map((c) => {
            const s = SECTIONS.find((x) => x.id === c.id)!;
            return (
              <section key={c.id} className={`s-cap s-cap-${c.layout}`} data-cap={c.id} aria-labelledby={`${c.id}-h`}
                       data-tone="night">
                <div className="s-grid s-cap-grid">
                  <div className="s-cap-in">
                    <p className="s-cap-n" aria-hidden="true"><span>{s.n}</span>{s.title}</p>
                    <h2 id={`${c.id}-h`} className="s-cap-h"><Words text={c.h} /></h2>
                    {c.p && <p className="s-cap-p">{c.p}</p>}
                    {c.layout === "stop" && (
                      <div className="s-actions s-cap-actions">
                        <a className="s-btn s-btn-paper" href="#demo">Book a demo</a>
                        <a className="s-btn s-btn-line" href={live} onClick={(e) => toApp(e, live)} {...prefetch}>Explore the platform</a>
                      </div>
                    )}
                  </div>
                </div>
              </section>
            );
          })}
        </div>
        <World ref={world} />
        <div className="s-dip" aria-hidden="true" />
        <p className="s-cue" aria-hidden="true"><span>Scroll</span><i /></p>
        <nav className="s-rail" aria-label="Story chapters">
          <ol>
            {SECTIONS.filter((s) => s.n).map((s) => (
              <li key={s.id}>
                <button type="button" data-section={s.id} aria-label={`${s.n} ${s.title}`}><span aria-hidden="true">{s.n}</span></button>
              </li>
            ))}
          </ol>
          <i className="s-rail-glide is-off" aria-hidden="true" />
        </nav>
      </div>
      <div className="s-flow">
        {SECTIONS.map((s, i) => (
          <div key={s.id} id={s.id === "home" ? undefined : s.id} className="s-spacer" data-nav={s.id === "home" ? "home" : "product"}
               data-chapter={s.n || undefined} data-title={s.title || undefined}
               style={{ "--span": (SECTIONS[i + 1]?.from ?? FILM_END) - s.from } as CSSProperties} />
        ))}
      </div>
    </div>
  );
}
