# Welcome Wave: live-action production document (blueprint Part F)

The landing page uses temporary stock footage: cottonbro studio's tech-office series on Pexels (`frontend/public/media/CREDITS.md`). This document is the brief for the custom shoot that replaces it.

The page does not change when the footage is swapped:

- **What the page needs.** One clip per shot listed below, at the stated length, with the same names.
- **How to deliver it.** Encode it as described in `public/media/welcome-wave/manifest.json` (its `encoding` notes) and drop it into the same folders.

## 1. Story in one breath

1. A new engineer on his first day faces an unfamiliar codebase.
2. He tries, writes notes, and walks away from the desk.
3. A senior engineer notices, comes over and explains the important part at the screen.
4. That explanation was recorded, and it becomes the product.
5. Back at the desk, he ships his first change.
6. The team works around him with confidence.

There is no dialogue and no on-screen text. All words are set by the website.

## 2. Characters and wardrobe (continuity is everything)

| Role | Casting | Wardrobe (identical in every shot) |
|---|---|---|
| **Vijay**, the new engineer | Mid-20s. Open, slightly unsure face; reads well in close-up. | A mid-grey zip hoodie over a small-check shirt in warm brown, dark trousers. No logos. Same hair every day of the shoot. |
| **Ravi**, the senior engineer | 30s to 40s. Calm, warm authority; a natural explainer with his hands. | A light shirt with a dark knit vest or cardigan, sleeves rolled once. Glasses optional, but the same in every shot. |
| **Team**, 2 to 3 colleagues | Varied ages, genders and backgrounds; relaxed. | Muted solids (navy, olive, charcoal, off-white). No stripes finer than 5 mm (moiré). No logos. |

**Continuity log.** Keep a sheet per setup: coffee mug position, chair height, monitor content, hoodie zip height, sleeve state.

## 3. Office requirements

- **The space.** A real working tech office, not a studio set:
  - large windows on one wall (soft daylight, slightly overcast);
  - dark curtains or dark walls on the other sides (they give the dark cinematic base the page needs);
  - pendant lamps over the desks;
  - a planning wall with sticky notes or a whiteboard about 6 to 8 m from Vijay's desk;
  - a long shared desk for the team shot.
- **Vijay's desk.**
  - Two monitors: the main one at 3/4 to camera and the second side-on.
  - A real keyboard (no RGB), a mug and one notebook.
  - Cables tidy but present.
- **Screens.**
  - Show a dark-theme editor with real-looking Python, no brand UIs.
  - For compositing, also roll every screen shot once with a clean dark-grey screen (#1e1e1e) and a 4-corner tracking marker set; see section 7.
- **Clearances.**
  - No visible logos, brand stickers, readable names, email or chat.
  - Get a location release and talent releases (website, worldwide, perpetual).

## 4. Camera direction

- **Camera.** A cinema camera at 4K DCI, 25 fps, 180° shutter, log profile, 10-bit. Gimbal or slider for moves; a locked-off tripod for loops.
- **Lenses.** 35 mm for wides, 50 mm for mediums, 85 mm for close-ups. Shoot at T2 to T2.8 for a shallow depth of field.
- **Moves.**
  - Slow and motivated: push-ins at 10 to 20 cm/s, one short tracking shot.
  - No handheld shake.
  - The page adds a digital push on top, so leave 10 % headroom on every side.
- **Loops.** Hold every loop shot for at least 15 s of natural, low-amplitude action (typing, small head turns), with no one entering or leaving the frame.
- **Scrubbed shots.** One continuous take, constant speed, no cuts. Viewers scroll these forwards and backwards, so any wobble shows.

## 5. Shot list and timing

The "page use" column is what the site does with each shot.

| # | Shot (manifest name) | Framing and action | Length to deliver | Page use |
|---|---|---|---|---|
| 1 | Office establishing (`hero`) | Wide: the whole office, the team at the long desk, Vijay visible at the right third. Locked off. Light movement only. | 15 s (we cut a 9 s loop) | Loop behind the cover headline, with a scroll-driven push towards Vijay. The left 50 % must be calm (the headline sits there). |
| 2 | Employee at desk, approach (`first-day`) | Medium-wide to medium: slow push-in on Vijay typing, the code monitor in the right foreground. | 6–8 s, one move | Scrubbed: scroll drives the approach. |
| 3 | Confused close-up (`first-day-close`) | 85 mm close-up: Vijay reads, frowns, leans in, exhales. Eye-line just off the lens, to the left. | 15 s | Scrubbed. |
| 4 | Over-the-shoulder monitor | OTS from behind Vijay's right shoulder: code on screen, his hand on the mouse. Also roll a clean-screen pass. | 10 s | Used inside shots 6 and 7 (screen compositing). |
| 5 | Walking away (`walking`) | Vijay finishes a note, stands and walks to the planning wall; the camera tracks with him. One take. | 10–12 s | Scrubbed: scroll drives the walk. |
| 6 | Mentor notices and approaches (`mentor-notice`) | Ravi and a colleague stop behind Vijay's chair and read his screen. Vijay half-turns. | 10 s | Scrubbed. |
| 7 | Mentor explains (`mentor`) | Ravi leans in and points at the code; the camera pushes from medium to the OTS monitor, ending framed on the screen. | 9–10 s, one move | Scrubbed. Its last frame must match the first frame of shot 8. |
| 8 | Into the screen (`product-transition`) | Continue from shot 7's end: locked OTS on the monitor, gentle typing. Also roll a clean-screen pass. | 8 s | Scrubbed with a digital push into the screen; the page becomes the product. |
| 9 | Hands and keyboard (spare) | 85 mm: Vijay's hands typing confidently, a natural rhythm. | 10 s | Spare insert or poster. |
| 10 | Working confidently, the PR (`first-contribution`) | Medium profile: Vijay typing with intent, then a small satisfied lean back as he submits. One take. | 10 s | Scrubbed on a screen in the 3D product world beside the journey; the lean-back lands as "Approved, merged". |
| 11 | Team collaboration (`team-impact`) | Medium-wide: the team around Vijay's desk, Ravi with notes, easy talk, someone points at the screen. | 15 s | Scrubbed on a screen in the 3D world, behind the leadership report (the camera's last stop). |
| 12 | Final office (spare) | Wide at dusk: lights on, the team leaving the desk area, warm windows. | 15 s | Spare (a dark finale or footer background). |

**Timing on the page** (in screens of scroll):

| Section | Length |
|---|---|
| The cover | 1 |
| Scene 1 | 3 (2.5 on phones) |
| Scene 2 | 2.5 |
| Scene 3 | 3 |
| Scene 4 | 4.2 |
| Scene 5 | 3.8 |
| Scene 6 | 3.2 |

Captions sit for the middle 80 % of each scene.

## 6. Lighting

- **Key and fill.** A soft daylight key from the windows (5600 K) and a negative fill on the camera side to keep contrast. The overall level is about 1 stop under a "corporate bright" look.
- **Practicals.** Pendant lamps on, dimmed, at 3200 K, so they glow warm in frame.
- **Monitor glow.** Monitors at 30–40 % brightness, so faces are lit by the screen without blowing out.
- **Skin.** Warm and natural. Shadows go cool in the grade.
- **Grade target** (matching the current stock):
  - lifted blacks no more than 2 %;
  - cool shadows, warm mid-tones;
  - saturation about 95 %;
  - no teal-and-orange cliché.
  - Teal is reserved for the product's "explained in a KT session" mark.
- **Night spare.** For shot 12, the city should be visible through the windows at blue hour.

## 7. Screen compositing

- **Clean plates.** Every shot with a readable screen also gets a clean-screen pass:
  - flat dark grey, `#1e1e1e`;
  - four small tracking crosses at the screen corners;
  - the same move.
- **No baked-in text.** Never bake headlines, UI or product names into the footage. The site sets all words and draws the product itself (React and SVG), so screens can be replaced by the real product later.
- **Delivery.** Deliver clean-plate passes as ProRes 4444 with matching timecode.

## 8. Transitions (what the page does, so the shoot can help)

The page is one continuous film locked to a smooth, velocity-capped scroll:
- Every clip is scrubbed by the scroll; the hero alone also plays by itself.
- Every shot keeps a slow camera move while it is on screen.
- When the reader stops, the page settles on the nearest story beat. Each shot therefore needs a clean, readable **rest frame** at its beat: eyes open, no motion blur, nobody mid-step.

There are no wipes. The cuts are motivated:

- **Push-in into a dissolve.** The camera leans into the wide office towards Vijay, and the shot dissolves into his medium shot while both keep moving.
  - Shoot shots 1 and 2 with Vijay in the same screen position (right third).
- **Dissolves over motion with a light leak.** Every dissolve overlaps about half a screen of scroll, both shots still moving, with a soft warm bloom from the window side.
  - Keep the window side consistent (frame-left) across setups.
- **Lateral continuity.** The walk away (shot 5) and the mentor's arrival (shot 6) share one lateral direction: the page carries the drift from one into the other.
  - Track shot 5 in the same direction the mentor enters shot 6.
- **Push-in match cut into the product.** Shot 7 ends over the shoulder on the monitor; shot 8 is the same take, and the camera pushes in until the screen fills the frame. The real product then emerges from the monitor's plane as panels in a dark 3D space.
  - The monitor must sit about a third in from the left, roughly level with the eye line.
  - Leave headroom for a 3x digital push.
- **Into the 3D world.** Shots 10 and 11 are shown as screens floating in the product world, not full-bleed. Frame them like monitors: subject centred, nothing important in the outer 8 %.

## 9. Desktop and mobile framing

- **Desktop.** 16:9, cropped from the DCI frame.
  - The headline sits in the left 45 % (scenes 1–2 and the cover) or the right 45 % (scene 3), over the lower half.
  - Keep faces out of those zones.
- **Mobile.** A 9:16 crop, 540 × 960, centred on the subject's horizontal position (manifest `focus`). Captions sit in the bottom third.
  - Faces belong in the middle third, vertically.
  - Every action must read inside a 9:16 window centred on the subject. Frame wides with the subject at least 15 % in from the edges, so both crops work.
- **Posters.** The first frame of every shot is its poster. It shows before the video, and is all reduced-motion and data-saver visitors see. Make it a good still: eyes open, no motion blur.

## 10. Delivery

- **Masters.** ProRes 422 HQ, graded and ungraded, plus the clean-plate passes.
- **Web encode.** Re-run the encode with the manifest's settings:
  - 1920 and 960 H.264;
  - VP9 WebM;
  - 9:16 phone cuts;
  - JPEG and AVIF posters;
  - scrubbed shots at 15 fps with a keyframe every 5 frames.
- **Budget.** At most 2 MB per clip at 1920 px and 2.5 MB for the hero.
- **Swap.** Replace the files in place, update the durations in `src/site/cinematic/film.ts` and in `manifest.json`, then update `CREDITS.md`.
