/* The footage: every full-bleed shot (a poster under a video), stacked in film order so each one dissolves in over the
   one before it, plus the film's finish: the light leak that blooms over a dissolve, a vignette and a fine grain.
   Decorative (aria-hidden): every word of the story is in the film's text layer. Nothing loads by itself; the controller
   fills in poster and video sources as each shot comes near. Each shot is three boxes: .s-shot (its dissolve),
   .s-shot-media (its camera move, set by the scroll) and .s-breath (the footage's slow breath, always running). */
import { CSSProperties, forwardRef } from "react";
import { posters, SHOTS } from "./film";

export const CinematicStage = forwardRef<HTMLDivElement>(function CinematicStage(_, ref) {
  return (
    <div ref={ref} className="s-stage" aria-hidden="true">
      {SHOTS.filter((s) => s.place === "stage").map((s, i) => {
        const p = posters(s);
        return (
          <div key={s.name} className="s-shot" data-shot={s.name} data-push={s.push ? "" : undefined} style={{ "--focus": s.focus, "--origin": s.origin, zIndex: i + 1 } as CSSProperties}>
            <div className="s-shot-media">
              {/* the held frame's slow breath (cinematic.css s-breathe) is this box's, not the video's: the compositor moves a
                  plain box by itself, where an animated <video> had the page repainted on every frame of a scroll */}
              <div className="s-breath">
                <picture>
                  <source type="image/avif" media="(max-width: 699px) and (orientation: portrait)" data-srcset={p.avifPortrait} />
                  <source media="(max-width: 699px) and (orientation: portrait)" data-srcset={p.jpgPortrait} />
                  <source type="image/avif" data-srcset={p.avif} />
                  <img className="s-poster" alt="" decoding="async" data-src={p.jpg} data-srcset={`${p.jpgSmall} 960w, ${p.jpg} 1920w`}
                       sizes="100vw" width="1920" height="1080" />
                </picture>
                <video className="s-video" tabIndex={-1} />
              </div>
            </div>
          </div>
        );
      })}
      <div className="s-leak" />
      <div className="s-finish" />
      <div className="s-grain" />
    </div>
  );
});
