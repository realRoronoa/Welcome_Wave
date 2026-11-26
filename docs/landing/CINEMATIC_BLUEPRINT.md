# Welcome Wave: live-action cinematic web experience (founders' blueprint v1.0)

Creative direction: live action. Interaction: hybrid scroll-driven storytelling. Visual theme: adaptive dark-to-light.
Framework: the existing React 18 + Vite + TypeScript application.

## A. Mission
Build a premium, cinematic, scroll-driven landing page. It tells one continuous story:
1. a new software engineer struggles to understand an unfamiliar codebase;
2. a senior engineer helps;
3. captured engineering knowledge is discovered through Welcome Wave;
4. the engineer makes a first successful contribution.

Human storytelling uses **live-action footage** (no animated or CGI people). Technical visualisations are rendered by the
website itself (React, CSS, SVG). The visitor controls the story by scrolling. This is a real production website.

## B. Architecture
- Inspect the existing frontend, the PRD, the animation and video dependencies, the reusable product components, the design tokens and typography, routing, baseline tests, and the existing performance and accessibility issues.
- Don't rewrite the application, don't modify the backend, and don't install new dependencies without approval.
- Give the landing page its own architecture inside the app. Suggested responsibilities: CinematicLandingPage, CinematicNavigation, CinematicStage, ScrollStoryController, StoryChapter, SceneVideo, SceneTransition, KnowledgeVisualization, ProductReveal, FirstContributionScene, LeadershipReveal, EnterpriseSecurity, DemoCTA.

## C. Scroll engine (hybrid)
- No scroll hijacking and no mandatory snapping. Never make the visitor wait. Scrolling works naturally in both directions.
- **Scroll-triggered chapters.** IntersectionObserver for chapter boundaries; requestAnimationFrame for synchronised progress.
- **Selective video scrubbing.** Scroll maps to clip time for these scenes:
  - the camera approaching the employee;
  - the employee walking away from the desk;
  - a selected mentor interaction;
  - the first contribution being completed.

  Handle metadata loading, seeking, reverse scrolling, interruption, decode failures, visibility changes, resizes and mobile. Don't update currentTime excessively. If native scrubbing is inconsistent, use an optimised image sequence for that scene.
- **Natural playback.** Other chapters play when active and pause or release when not.
- **Product transitions.** Actual React-rendered interfaces: restrained CSS perspective and layered transforms, SVG connections for code relationships, clip and opacity between footage and product. No new rendering dependencies (no Three.js) without approval.

## D. Storyboard (six scenes)
1. **The first day.** A new engineer at a desk in a modern tech office facing an unfamiliar codebase.
   - Camera: wide, slow push-in, over-the-shoulder monitor shot, close-up.
   - Headline: "Your first day shouldn't feel like your hundredth unanswered question."
   - Support: "Every new engineer deserves more than a repository link and a meeting invitation."
   - Scroll controls the camera approach.
2. **The struggle.** He switches between files and docs, then gets up and walks away from the desk.
   - Camera: a tracking shot.
   - Headline: "The knowledge exists. Finding it shouldn't depend on who happens to be online."
   - Scroll controls the walk.
3. **The mentor.** A senior engineer notices him and approaches; they return to the desk; the senior explains an important part of the system.
   - Headline: "What if the right explanation was already waiting for you?"
   - The explanation bridges into the knowledge visualisation.
4. **Knowledge transformation.** The office transitions into the real product. It shows:
   - a recorded explanation;
   - the repository with its exact files and lines;
   - connected knowledge;
   - verified answers;
   - a learning path.

   Headline: "Capture once. Connect to code. Reuse everywhere." Genuine React components with realistic demo data, never fabricated screenshots.
5. **The first contribution.** Back at the desk, he now understands. His journey: learning path → recorded explanation → first scoped task → private pre-submission review → pull request → review and approval.
   - Headline: "From the first question to the first contribution."
6. **Business impact.** He works confidently with the team, then the real leadership report. It shows:
   - time to first merged PR;
   - onboarding progress;
   - KT coverage;
   - questions resolved without a senior;
   - senior time saved;
   - knowledge risk.

   No invented customer statistics: demo values are labelled illustrative.
   - Headline: "Better onboarding. Stronger engineering teams."
   - CTAs: "Book a demo" (primary), "Explore the platform" (secondary).

## E. Adaptive design
1. **Cinematic.** Dark charcoal, realistic footage, subtle white type, restrained motion.
2. **Knowledge transformation.** Dark footage turns into the technical interface; teal only for recorded explanations; connected files and code.
3. **Product.** A clean light interface: Geist, monochrome, fine borders, real dashboards, clear hierarchy.
4. **Conversion.** Back to sophisticated dark or neutral: a strong final headline, Book a demo, enterprise security and trust.

## F. Live-action production document (for a custom shoot)
It covers:
- characters, wardrobe continuity and office requirements;
- camera direction, the shot list and scene timing;
- lighting;
- screen compositing (screens kept clean for product compositing; no headlines baked in);
- transitions;
- desktop and mobile framing.

Required footage:
- an office establishing shot;
- the employee at the desk;
- a close-up of the confused employee;
- the over-the-shoulder monitor shot;
- walking away;
- the mentor noticing, approaching and explaining;
- hands and keyboard;
- working confidently;
- the PR interaction;
- team collaboration;
- the final office shot.

## G. Video assets
- A manifest under public/media/welcome-wave/: hero, first-day, walking, mentor, product-transition, first-contribution, team-impact, posters, mobile.
- Optimised desktop clips plus mobile versions, posters, fallbacks, durations, and the scene-to-section mapping.
- Broadly compatible encoding.
- Lazy-load below the fold; preload only the initial media and the next scene's metadata. Never download the whole film on first load.

## H. Accessibility and performance
- Video muted and never with audio; prefers-reduced-motion respected with static alternatives.
- Responsive and keyboard accessible, with accessible navigation; no scroll hijacking.
- No horizontal overflow, no needless background work, no layout shift, and poster fallbacks for every video.
- Test at 1440, 1024 and 390. Lighthouse ≥ 95 and WCAG 2.2 AA.

## I. Checkpoints
1. Technical audit.
2. Cinematic prototype (temporary assets labelled).
3. Product integration (real interfaces).
4. Production asset integration.
5. Final QA (tests, accessibility, responsiveness, browsers, performance, screenshots, a working demo).

At each checkpoint: what changed, what passed, what remains, and which decisions need approval.

## Final quality standard
A premium live-action technology commercial that turns into a real enterprise application.

- It tells the story without long explanations.
- The film is real footage, and the product is real software.
- Scrolling works in both directions.
- The page stays fast, responsive and accessible.
- Never claim completion while temporary footage, mockups or non-functional screens remain.
