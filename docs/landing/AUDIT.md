# Landing page: technical audit (Checkpoint 1)

Date: 2026-11-14. Scope: `frontend/` (the public site), before and after the cinematic rebuild.

## Before the rebuild

- **Stack.**
  - React 18.3, Vite 5.4, TypeScript 5.5.
  - The public site (`src/site/`) is its own chunk; the app (`AppEntry`) loads only after "Log in" or "Get started".
  - Fonts: Geist and Geist Mono from Google Fonts.
- **Landing story.**
  - `Story.tsx` plus `site/office/*` (about 1,500 lines): a white "clay" three.js office with mannequin figures, as a lazily imported chunk.
  - Six pinned chapters, driven by one scroll clock (`site/clock.ts`: IntersectionObserver plus requestAnimationFrame; idle when the page is still).
- **Theme.** Paper (`#f2f2f2`) and ink, with teal only for "explained in a KT session". The agents band, the security band, the finale and the footer were already black.
- **Intro.** `index.html` and `public/boot-*.js` play a 5-second intro on every load of the site, in its paper variant (`boot-theme.js` forces `data-theme="light"` on `/`).
- **Animation and video dependencies.**
  - `three` (^0.181) was the only animation dependency.
  - There was no video and no media folder.
- **Reusable product components.**
  - `lifecycle/Timeline.tsx` and `lifecycle/ReportParts.tsx` import `lifecycle/kit.tsx`. That file pulls in the API client (`api.ts`, `live.ts`) and `styles/lifecycle.css`.
  - `pages/kt-parts.tsx` imports `api.ts`.
  - `pages/chat-parts.tsx` imports `styles/chat.css` (40 KB of app CSS).
  - Importing any of these would put app code or app CSS into the site chunk.
  - Their data shapes (`lifecycle/types.ts`: `Milestone`, `ReportFigure`) are type-only and import cleanly.
- **Routing.**
  - `surface.ts` picks site or app by path.
  - In-page anchors are handled in `handover.ts`.
  - The nav's folio reads `[data-nav][data-chapter][data-title]` sections.
- **CSP** (`nginx.conf.template`).
  - `media-src 'self' blob:` allows same-origin MP4 and WebM and blob-URL scrubbing.
  - `img-src 'self' data: blob:` allows posters.
- **Tests.** The site has no unit tests. Earlier QA used Playwright scripts (scratchpad).
- **Known issues.**
  - The intro dissolves into the page in its paper colours.
  - The 3D chunk was heavy on low-end GPUs.
  - There was no live-action footage.

## After the rebuild

- **Removed.** `Story.tsx` and `site/office/*` (the clay office and its three.js chunk).
  - Nothing in `src/` imports `three` any more; only `package.json` lists it.
  - `three` and `@types/three` can be uninstalled.
- **New.**
  - **Film code** (`src/site/cinematic/`):
    - `film.ts`: shots and variants;
    - `SceneVideo.ts`: per-clip loading, scrubbing and playback;
    - `ScrollStoryController.ts`: the scroll engine;
    - `CinematicStage.tsx`;
    - `CinematicLandingPage.tsx`;
    - `product/`: site-local copies of Timeline and FigureTile, the code view, and scenes 4–6;
    - `cinematic.css`.
  - **Media** (`public/media/welcome-wave/`): the clips, `mobile/`, `posters/` and `manifest.json`.
  - **Credits:** `public/media/CREDITS.md`.
- **Edited.**
  - `Site.tsx`: wires in the film; the landing page is dark (`html.s-dark`, theme-color `#0a0a0a`).
  - `Footer.tsx`: feature links now point to the new scene anchors; "Explore the platform".
  - `site.css`: the old story, still and office rules are removed.
- **Untouched.**
  - `Nav.tsx`, `handover.ts`, `clock.ts`, About, the 404, `index.html`, the app and the backend.
  - `public/boot-theme.js` (outside this track's files). The intro still plays its paper variant on `/`, then dissolves into the dark hero.
  - To make the intro dark on the landing page, `boot-theme.js` should set `data-theme="dark"` and theme-color `#0a0a0a` when the path is `/`.

## Round 2: one continuous film (2026-11-14)

The founders asked for motion like a story, not slides, and for a black ending with a 3D theme.

- **Motion.**
  - **Before:** discrete pinned scenes with wipes and timed caption swaps.
  - **After:** one sticky view driven by a damped scroll position, frame-rate independent.
  - Every clip is scrubbed: the four former loops were re-encoded at 15 fps with a keyframe every 5 frames. The hero alone still plays by itself.
  - Shots dissolve over motion with a light leak. The walk and the mentor share one lateral move. The camera pushes into the monitor until it becomes the product.
  - Captions are split into lines that rise and drift at their own depth.
- **Theme.** The light product stage is gone: scenes 4–6 are a dark CSS 3D world.
  - Real product panels float in depth over a grid floor in fog.
  - Teal threads are drawn on a canvas from the KT moment to the code and the answer.
  - Pointer parallax on desktop; a 2.5D fly-through on phones.
- **Headlines.** The six headlines and the cover headline are unchanged. Scene 6 with "Book a demo" and "Explore the platform" is the camera's last stop. The finale, agents, security and footer bands stay black.
- **Logo.** The footer's sign-off now uses `Logo` from `ui.tsx`, so the brand agent's new mark shows there and in the nav.
- **Still no `three`.** Nothing imports it.

## Round 3: smooth scroll and a locked film (2026-11-14)

The founders asked for a scroll that feels premium and a film that stays in sync with it. They did not want the video to race on a fast scroll, and they did not want the film to sit mid-action.

**Scroll** (`site/cinematic/SmoothScroll.ts`, new). It is an inertial smooth scroll with no dependency:
- **Wheel.** It is intercepted on the landing page only. `deltaMode` is normalised, and each event's contribution is clamped.
- **Damping and caps.** Damping is frame-rate independent and settles in about 0.7 s. The target may lead the glide by at most 1.2 screens, and the speed is capped at 2.2 screens a second.
- **Keyboard and links.** The keyboard and in-page links glide too. Long jumps cut there under a short dissolve.
- **Following other scrolling.** It follows the scrollbar, find-in-page and native jumps at once.
- **Native where it should be.** Touch keeps native momentum; reduced motion keeps native scrolling.

**Film.** The film position is a straight function of the scroll: 0.7 screens per film second. Every clip except the hero loop is seeked to it (short-GOP clips, one seek in flight).

**Settle.** After 180 ms without input, the page glides to the next beat in the direction of travel if the reader was a fifth of the way there; otherwise it glides back. The film never rests mid-action.

**Kept from the previous round:**
- the chapter rail (six ticks, clickable);
- the scroll cue on the cover;
- captions that rise in and leave upwards;
- the removed footer wordmark.

**Performance.**
- Captions and faded layers are pre-promoted.
- Posters are hidden once their video is ready, and decoded ahead.
- Decoders are set up only while the page is still.
- The world's screens use the 960 cut.

## Round 4: final scroll polish (2026-11-14)

**Two scroll feels, chosen per gesture.**
- **Trackpads** stream small, fast deltas (macOS reports them with `wheelDeltaY = -3 * deltaY`). They get a light touch: damping 14/s, a lead of at most 0.6 screens, and a cap of 3.2 screens/s.
- **Mouse wheels** keep the weighted glide: damping 7/s, a lead of 1.2 screens, and a cap of 2.2 screens/s.
- **Settling, keys and links** use the weighted glide.

**Hitches removed.**
- **Clip priming.** The two clips ahead of the reader and the one behind are primed while the page is still: the decoder is set up and the entry frame decoded. Decoder setup now waits until 450 ms after the last scroll.
- **Product world.**
  - It is drawn once, almost invisible, at the first quiet moment on the cover.
  - Its panels and floor hold their raster scale (`will-change: transform`), so they are not re-rastered as the camera starts moving again.
  - Empty thread canvases are no longer cleared every frame.
- **Seek watchdog.** A seek whose frame never reports back is released.

**Touches.**
- Captions lift a few pixels with the film's velocity; their supporting lines trail and the caption dims slightly.
- The film grain grows a little stronger while the film moves.
- A marker glides between the chapter rail's ticks.
