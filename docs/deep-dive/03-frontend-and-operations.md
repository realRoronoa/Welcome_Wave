# Part 3: the web app and how it ships

This part covers everything a browser downloads, and everything that puts it in front of people: the boot sequence, the
workspace, the pages, the landing film, the web server, the three ways to run Welcome Wave, CI and the tests.

Checked against the code on 2026-11-18. Where an older document disagrees with this one, the code decides.
Part 1 is [the backend and its data](01-backend-and-data.md). Part 2 is [agents and answers](02-agents-and-answers.md).

## Read this first

| If you want to know | Read |
|---|---|
| What happens between typing the address and seeing a page | [2. Boot](#2-boot-from-the-first-byte-to-the-first-page) |
| How a page is chosen, and who may see it | [3. The workspace shell](#3-the-workspace-shell), [6. Every page](#6-every-page) |
| How the browser talks to the API, and how pages stay current | [4. Talking to the server](#4-talking-to-the-server) |
| What happens when someone asks a question | [7. The Ask page and the trace](#7-the-ask-page-and-the-trace) |
| How a 1 GB recording gets from a laptop to the Note-taker | [8. Uploading a recording](#8-uploading-a-recording) |
| Why everything is black, white and one teal | [9. The design system](#9-the-design-system) |
| How scrolling drives the landing film | [10. The landing page](#10-the-landing-page) |
| What the web server allows and refuses | [12. The web server and its CSP](#12-the-web-server-and-its-csp) |
| How to run, release, back up and restore | [13. Operations](#13-operations) |
| What holds all of this in place | [14. CI](#14-ci), [15. The tests](#15-the-tests) |
| Which numbers are load-bearing | [16. Constants that matter](#16-constants-that-matter) |
| Which files must change together | [17. Things that must stay in step](#17-things-that-must-stay-in-step) |

The whole part in eight lines:

1. One HTML file serves three separate programs: the public site, the app, and a new hire's pre-boarding page.
2. An intro written in plain HTML and CSS covers the page while JavaScript loads. The app tells it when to leave.
3. The app asks the API for its configuration, signs the person in, then loads the workspace.
4. The workspace is one shell with a hand-written router. Each page is its own file, fetched when first opened.
5. Pages read through `api()`. One long-lived stream tells them when to read again. The stream never carries data.
6. The server decides who may see what. The role checks in the browser only decide what to draw.
7. nginx serves the built files, forwards `/api` to the API, and sends a strict Content-Security-Policy.
8. The same two Docker images run in the demo, on one self-hosted machine, and on Google Cloud.

Words used throughout, each defined once:

- **Surface**: one of the three programs that share `index.html` (site, app, portal).
- **Chunk**: one JavaScript file the build produces. The browser fetches a chunk only when the code in it is needed.
- **Shell**: the frame around every workspace page: the rail (the dark menu on the left), the top bar, and the page slot.
- **Hash route**: the part of the address after `#`, such as `#/meetings/mtg-1?t=55`. The workspace routes on it.
- **Stream**: a long HTTP response that the server keeps writing to (Server-Sent Events).
- **Topic**: a short name on a stream event, such as `tasks`, that says which kind of thing changed.
- **CSP**: Content-Security-Policy, a response header that tells the browser which scripts, styles and hosts it may use.
- **Token** (design): a CSS variable such as `--ink` that every stylesheet uses instead of a literal colour.
- **Film second** (`F`): the landing film's own unit of time. Scroll position is converted to `F`.

---

## 1. The shape of the web app

The web app is a React 18 single-page app written in TypeScript and built by Vite. It has three runtime dependencies:
`react`, `react-dom` and `firebase` ([package.json](../../frontend/package.json)). There is no router library, no state
library, no CSS framework and no animation library. Each of those jobs is a small file in this repository. That is on
purpose: the CSP stays strict (section 12), the download stays small, and a dependency audit has little to read.

### Three surfaces, one HTML file

[surface.ts](../../frontend/src/surface.ts) holds the rule that picks the surface from the address:

| Address | Surface | What it is |
|---|---|---|
| `/welcome/<token>` | portal | A new hire's pre-boarding page. No sign-in: the link is the permission. |
| `/login`, `/signup`, `/github/callback`, `/app…` | app | Sign-in and the workspace. |
| `/#/…` | app | An old link into the app. `main.tsx` moves it to `/app#/…`. |
| anything else | site | The landing page (`/`), `/about`, and a 404 page for every other path. |

Each surface is its own chunk. A visitor to the landing page never downloads Firebase or the workspace. A signed-in
engineer never downloads the film's code. A new hire on the pre-boarding page downloads neither.

`surfaceFor()` is repeated in two other places that run where this module cannot: once in
[boot-theme.js](../../frontend/public/boot-theme.js) (it runs before any bundle loads) and once in
[nginx.conf.template](../../frontend/nginx.conf.template) (it decides which paths get the HTML at all). Section 17 lists
this and the other rules that live in more than one file.

### The build

[vite.config.ts](../../frontend/vite.config.ts) does two things. In development it forwards `/api` and `/webhooks` to the
API on `localhost:8080`, with `xfwd: true` so the API sees the real caller address and its per-address rate limits work.
In the build it puts React into a chunk called `react`, so the site and the app share one copy instead of each carrying
their own. Everything else is split automatically wherever the code says `import("…")`.

`npm run build` runs `tsc -b` first, so a type error fails the build.

---

## 2. Boot: from the first byte to the first page

### 2.1 The intro is HTML, not JavaScript

[index.html](../../frontend/index.html) contains a full-screen intro, `#ww-boot`: the Welcome Wave mark draws itself as
four strokes, settles into the logo, the name rises, and three words appear. All of it is inline `<style>` and inline
SVG. It paints with the first frame, before any script has arrived, so a slow network shows a designed screen instead of
a white one.

The timeline is written as a comment at the top of that `<style>` block. The key moments, in seconds from first paint:
the W is drawn by 1.51, in place by 2.52, the name is up by 3.13, the words by 3.75. Two CSS variables size it: `--m` (the
mark's box) and `--k` (how much larger the W starts, 2.75).

Three small scripts sit in `frontend/public/`. They are files, not inline `<script>` blocks, because the CSP forbids
inline scripts.

| Script | Runs | Decides |
|---|---|---|
| [boot-theme.js](../../frontend/public/boot-theme.js) | In `<head>`, before paint | Light or dark for the intro. Site pages are light. The landing page gets `ww-boot-dark` because it opens on a dark film. App pages read the saved choice from `localStorage["ww-theme"]`. The pre-boarding page gets `ww-no-boot`, which hides the intro entirely: a link from an email should show the page. It also sets the browser chrome colour (`<meta name="theme-color">`). |
| [boot-words.js](../../frontend/public/boot-words.js) | Right after `#ww-boot` | Picks the three words (14 sets, never one of the last five shown in this tab). Records the first frame's time in `window.__wwBootT0`. Turns Esc, Enter, Space, Tab, a click, a tap or a scroll into a skip. Runs the failsafe described below. |
| [boot-field.js](../../frontend/public/boot-field.js) | After that | Draws the field of small strokes on a canvas, the teal pen that rides each stroke, and the exit: the page appears through a W-shaped window (`REVEAL_MS = 820`). Without it the intro still works and ends in a plain fade. |

**The failsafe.** If the app never takes over, the hint line "esc to skip" would be a dead promise. `boot-words.js`
replaces it with a message and a Reload button in three cases: a `<script>` failed to load (at once), nothing has
answered 12 s after the first frame (`STALL_MS`), or the viewer skipped and nothing answered within 3 s
(`SKIP_STALL_MS`). It stands down as soon as `window.__wwBootReady` is true.

### 2.2 `boot.ts`: the hand-off

[boot.ts](../../frontend/src/boot.ts) exports one function that matters: `hideBoot()`. A surface calls it when it has
something real to show. `hideBoot()` does not remove the intro at once. It lets the intro play to `MIN_MS = 4150` of its
own timeline, unless the viewer has skipped, then adds the class `ww-boot--done` (the exit starts), and removes the node
`FADE_MS = 850` later.

Three details are worth knowing:

- **Which clock.** `played()` measures time from the intro's own CSS animations, not from navigation start. On a cold
  start the first paint can be seconds late. Measured from navigation, the intro would be cut short on exactly the slow
  loads it exists for.
- **Reduced motion.** With `prefers-reduced-motion`, the intro is a still card held for `REDUCED_MS = 850` and then faded.
- **The browser chrome colour.** While the intro is up, a `MutationObserver` keeps `<meta name="theme-color">` on the
  intro's field colour, remembers the colour the app wanted, and puts it back as the intro leaves. It then fires the
  window event `ww-boot-done`.

Who calls `hideBoot()`: the site ([Site.tsx](../../frontend/src/site/Site.tsx)), the sign-in frame
([Entry.tsx](../../frontend/src/auth/Entry.tsx)), any `Splash` card ([ui.tsx](../../frontend/src/ui.tsx)), the workspace
([Workspace.tsx](../../frontend/src/Workspace.tsx)), and the crash screen in `main.tsx`.

Who waits for `ww-boot-done`: the workspace (to start fetching the other pages' code), the landing page's cover (to start
its entrance), and the sign-in terrain.

While the intro is still up, `Booting()` in `ui.tsx` renders nothing at all. A loading card under the intro would only
flash when the intro lifts.

### 2.3 `main.tsx`: pick a surface, survive a bad chunk

[main.tsx](../../frontend/src/main.tsx) is the entry module. In order:

1. `installErrorReports()` (section 11).
2. `normalize()` tidies the address: `/index.html` becomes `/`, a site path loses its trailing slash, `/#/chat` becomes
   `/app#/chat`. It uses `history.replaceState`, so no extra Back step appears.
3. `Root` renders one of `Site`, `Portal` or `AppEntry`, each a `React.lazy` import.

`Root` listens for `popstate` and for the custom event `ww:navigate` (sent by `navigate()` in `surface.ts`). A surface
switch runs inside a React transition. The effect is that the page on screen stays until the other surface's chunk has
arrived, and a 2 px hairline (`Pending`) shows at the top meanwhile. The comment in the file explains the one subtle
part: there is a single `<Suspense>` **outside** the keyed error boundary. A Suspense inside it would be a new boundary
on every switch and would show its empty fallback at once.

**A chunk that will not load.** Built files have a hash in their name. After a deploy the old names are gone, so a tab
opened before the deploy fails the next time it needs a new chunk. `isChunkError()` (in
[report.ts](../../frontend/src/report.ts)) recognises that failure. `reloadOnce()` then reloads the page, once, guarded by
`sessionStorage["ww-chunk-reload"]`. If the page has already reloaded for this reason, it shows "Couldn't load Welcome
Wave" instead of looping. `Settled` clears the guard once a surface has really rendered. The same remedy answers Vite's
`vite:preloadError` event.

Any other crash is reported, given a short reference id, and shown as "Something went wrong" with that id. A crash is
never blamed on the connection and never reloads away what someone typed. The `Unavailable` screen uses inline styles
only, because neither surface's stylesheet may have loaded.

### 2.4 `AppEntry` and `App`: configuration first

[AppEntry.tsx](../../frontend/src/AppEntry.tsx) is the app's chunk. Its import order is the point: `./theme` first (it
applies the saved light or dark choice when imported, before anything paints), then `styles.css` and `shell.css`, then
`App`. Page stylesheets imported later refine these.

[App.tsx](../../frontend/src/App.tsx) asks the API one question before drawing anything: `GET /api/config`. The answer
(`Config` in [api.ts](../../frontend/src/api.ts)) says which mode the server runs in and which features exist:
`mode`, `firebase`, `signup_enabled`, `github_app`, `ai`, `repo_by_url`, `recording_upload`, `google_calendar`,
`calendly`, `email`, `demo_password`. The web app has no build-time switches for these. One build works against any
server.

- If the request fails: "Welcome Wave is unreachable" with a Try again button.
- If `mode` is `demo`: `DemoSession`.
- Otherwise: `AuthGate`.

The workspace is yet another chunk (`lazyPage(() => import("./Workspace"))`). `App` starts fetching it 600 ms after
mount, so it downloads while the person is still typing a password.

Three small functions in `App.tsx` move the address around sign-in:

- `enterApp(me, afterSignIn)`: `/login` and `/signup` become `/app`. A deep link opened while signed out is kept. It
  also sets `localStorage["ww-signed-in"]`, a hint that lets the landing page say "Open Welcome Wave" instead of
  "Log in". It is only a hint: the app always checks the session itself.
- `landOnRolePage()`: when the address names no page, go to the role's start page. Every role starts on `#/home`
  (`LANDING`); the Home page adapts to the role.
- `leaveApp()`: sign-out. It clears shared reads, removes the Ask page's per-tab memory (`ww-routes:*`), and sets the
  address to `/login`, so the next person on a shared machine starts clean.

### 2.5 Signing in

**Demo mode** ([DemoSignIn.tsx](../../frontend/src/auth/DemoSignIn.tsx)). The page lists the demo accounts
(`GET /api/demo/people`). Signing in posts the email and the one demo password to `POST /api/demo/login`. The member's
id is then kept in `localStorage["ww-demo-user"]` and sent on every call as the header `X-Demo-User`. That header is the
whole session. This is why the demo API must never be reachable from another machine: `docker-compose.yml` binds it to
`127.0.0.1`. If a stored id gets a 401 or 403 from `GET /api/me`, the demo data was reset; the page signs out and says
so.

**Every other mode** ([auth/index.tsx](../../frontend/src/auth/index.tsx)). `AuthGate` is a small state machine. Its
`Phase` is one of `loading`, `signed_out`, `unverified`, `no_account`, `invited`, `ready`, `error`.

1. It imports [firebase.ts](../../frontend/src/auth/firebase.ts) dynamically. That is the only place the Firebase SDK is
   referenced, so the demo never downloads it.
2. `init(config.firebase)` starts Identity Platform (Firebase Auth). `setTokenSource(m.token)` gives `api.ts` a function
   that returns a fresh ID token.
3. `onUser` (Firebase's `onIdTokenChanged`) fires with a user or with `null`. With `null`, the phase is `signed_out`.
   With a user whose `uid` differs from the last one seen, `loadMe()` runs. The `uid` check matters: token refreshes
   fire the same callback every hour, and without the check each refresh would reload the account.
4. `loadMe()` calls `GET /api/me`. Success means `ready`. A 403 carries a code that picks the next screen:

| 403 code | Phase | Screen |
|---|---|---|
| `email_not_verified` | `unverified` | `VerifyEmail` in [Setup.tsx](../../frontend/src/auth/Setup.tsx) |
| `invitation_pending` | `invited` | [Invited.tsx](../../frontend/src/auth/Invited.tsx). Joining is a button (`POST /api/invitations/accept`), never automatic. |
| `no_account` | `no_account` | `Signup` in `Setup.tsx` (`POST /api/signup`), if `signup_enabled` |

`seq` is a counter that makes late answers harmless: every `loadMe()` takes a number, and an answer whose number is no
longer the newest is dropped.

The sign-in form itself is [SignIn.tsx](../../frontend/src/auth/SignIn.tsx). All of these screens sit in `EntryLayout`
([Entry.tsx](../../frontend/src/auth/Entry.tsx)): a black brand panel beside the form. The panel's moving contour lines
are [terrain.ts](../../frontend/src/auth/terrain.ts), hand-written WebGL with no library. Without WebGL the panel shows a
still SVG field instead. One patch of the terrain is teal, for the usual reason: it stands for "explained in a KT
session".

### Example A: a first visit to `/login` on a production server

1. nginx answers `/login` with `index.html` (never cached) and the CSP header.
2. `boot-theme.js` sees an app path, reads `ww-theme`, sets `data-theme`. The intro paints in that theme.
3. `boot-words.js` picks "Ask · Learn · Ship", starts the 12 s stall timer. `boot-field.js` starts the canvas.
4. `main.tsx` loads. `surfaceFor("/login", "")` is `app`. `AppEntry` is fetched; `theme.ts` and the styles load with it.
5. `App` calls `GET /api/config`. `mode` is `gcp`, so `AuthGate` renders. `Booting()` returns `null`: the intro is up.
6. `AuthGate` imports `firebase.ts`, calls `init`. Firebase reports no user. Phase: `signed_out`.
7. `SignIn` renders inside `EntryLayout`, which calls `hideBoot()`. Say the intro has played 2.1 s by then: it runs on
   to 4.15 s, opens the W-shaped window, and is removed 0.85 s later. `ww-boot-done` fires as the exit starts.
8. Meanwhile, 600 ms after `App` mounted, the workspace chunk started downloading.
9. The person signs in. Firebase fires `onUser` with a user. `loadMe()` gets `Me` (the person, their organisation, their
   projects). `enterApp()` rewrites the address to `/app#/home`.
10. `Workspace` renders, calls `startLive()`, and 1.2 s later begins fetching the other pages' chunks while idle.

---

## 3. The workspace shell

[Workspace.tsx](../../frontend/src/Workspace.tsx) is the frame around every page. It is one component of about 650
lines, and it is where most cross-cutting decisions live.

### 3.1 The router

`useHash()` is the whole router. It keeps `location.hash` in state, listens for `hashchange`, and splits the hash into a
`path` and `URLSearchParams`. `section` is the first path segment (`/meetings`), `id` the second.

Hash routing was chosen because it needs nothing from the web server: nginx serves `/app` and the browser does the rest.
Real paths are used only where an outside party chooses the address: `/github/callback`, `/login`, `/signup`,
`/welcome/<token>`.

One wrinkle is handled by [route.ts](../../frontend/src/route.ts). `history.replaceState` changes the address without
firing `hashchange`. A page that tidied its own query (the Ask page dropping `?q=`) left the router holding the old
address. `replaceRoute()` rewrites the address and dispatches `ww:route`, which `useHash` also listens for. Pages must
use `replaceRoute()`, never `history.replaceState` directly.

### 3.2 Who sees which page

The role comes from `me.role`. Five flags are derived once:

| Flag | True for |
|---|---|
| `manager` | `manager` and `admin` (`isManager()` in `api.ts`) |
| `admin` | `admin` |
| `exec` | `executive` |
| `hire` | `new_hire` |
| `expert` | `expert`, `manager` and `admin` |

The page is then chosen by one long `if / else if` chain. A page the role may not open renders `deny` ("Your role does
not include this page"). An unknown address renders `notFound`. Executives are handled first and narrowly: Home, Reports,
Agents (read-only) and their account, nothing else.

**This chain is not the security boundary.** It decides what to draw. The API checks every request against the
permission table in [docs/PERMISSIONS.md](../PERMISSIONS.md), and
[test_permissions_matrix.py](../../backend/tests/test_permissions_matrix.py) calls every route as every role to prove
it. If the two ever disagree, the person sees an empty or failed page, not someone else's data.

The rail (the left menu) is built from the same flags, in `groups`. Each item has a letter, and "g" then that letter
navigates (`useGoKeys`, armed for 1.4 s). Pages a role may open but that are not in its rail are added to the command
palette in the `more` list.

### 3.3 The project

A person can belong to several projects. `initialProject()` picks the one last used
(`localStorage["ww-project:<userId>"]`), else the demo project in demo mode, else the first. The chosen project goes into
`SessionContext` ([session.ts](../../frontend/src/session.ts)); every page reads it with `useSession()`.

`<main>` is keyed by the project id. Switching project therefore unmounts and remounts the whole page: no page has to
remember to refetch. `switchProject()` also drops the id and query from the address, because a meeting or review id
belongs to the old project. Links from the API and from notifications carry `?project=`; the shell follows it if the
person is a member, and re-reads `GET /api/me` once if the project is new to it.

### 3.4 The page slot

```
<main key={project.id}>
  <SessionContext.Provider>
    <div className="page-enter" key={`${path}:${tick}`}>
      <ErrorBoundary key={path}>
        <Suspense fallback={<LcSkeleton />}>{page}</Suspense>
```

- `page-enter` replays a 0.42 s fade-and-rise on each route. `tick` is bumped when the demo clock moves, which remounts
  the page so it reloads its data.
- `ErrorBoundary` ([ui.tsx](../../frontend/src/ui.tsx)) is keyed by route. One broken page shows "This page ran into a
  problem" with a reference id; navigating away resets it.
- `Suspense` shows a skeleton only the first time a page's chunk is fetched.

### 3.5 Pages load on demand

[lazyPage.ts](../../frontend/src/lazyPage.ts) wraps `React.lazy` with three additions:

- Once a chunk has arrived, the page renders synchronously. `React.lazy` alone would show the fallback for a frame every
  time.
- A failed fetch is forgotten, and a fresh `lazy()` replaces the rejected one. React keeps a rejected lazy rejected
  forever, so without this "Try again" could never work.
- `preloadPages()` fetches every page's chunk, one at a time, only when the browser is idle and the person has not
  scrolled, typed or tapped for 1 s. A chunk's code runs when it arrives, and that work must not land mid-scroll.

Home is not lazy: it is the first page for every role, so it ships with the shell.

### 3.6 Scrolling

[scroll.ts](../../frontend/src/scroll.ts) keeps scrolling native. Nothing in the app intercepts the wheel or touch,
smooths, or snaps. The module adds three things around the browser's own scrolling:

- **Position per history entry.** Each history entry gets an id in `history.state.wwScroll`. Positions are saved per id,
  in memory and in `sessionStorage["ww-scroll"]` (the newest 60). A new page starts at the top. Back and Forward return
  to where the page was left. The browser's own restoration is switched off (`scrollRestoration = "manual"`) because it
  runs before the page's data has loaded and lands short.
- **Waiting for height.** `restore(y)` scrolls as far as it can, then watches the body with a `ResizeObserver` and
  finishes the jump when the content is tall enough. It gives up after 3 s or the moment the reader scrolls, types or
  taps.
- **The top bar's hairline.** Where CSS supports scroll-driven animations it draws the line itself; otherwise the module
  toggles `html.ww-scrolled`, and only when the page crosses its top.

### 3.7 Everything else in the shell

- **Command palette** ([CommandPalette.tsx](../../frontend/src/CommandPalette.tsx)): ⌘K or Ctrl+K, or "/" outside a text
  field. A hand-written fuzzy matcher (`fuzzyMatch`). The shell builds the command list from the role. A typed question
  becomes `#/chat?q=…`.
- **Notifications** ([Notifications.tsx](../../frontend/src/Notifications.tsx)): `GET /api/notifications`, reloaded on
  the `notifications` topic. `linkTarget()` converts the server's path links into hash routes.
- **Breadcrumbs**: computed from the route. A page with a named item (a hire's program) adds the name through
  `CrumbContext` (`useCrumb` in [kit.tsx](../../frontend/src/lifecycle/kit.tsx)).
- **The drawer**: under 900 px the rail becomes a modal drawer. The page behind is made `inert`, Escape closes it, and
  focus returns to the menu button.
- **Focus after navigation**: after a route change, if focus was lost, it moves to the new page's `<h1>`, and a polite
  live region announces the page name to screen readers.
- **"Reconnecting…"**: shown in the top bar when the live stream is offline.
- **AI off**: when `config.ai === false` (self-hosted without a Gemini key), managers see a banner saying which features
  are off and how to turn them on.
- **The demo clock** ([DemoClock.tsx](../../frontend/src/lifecycle/DemoClock.tsx)): demo mode only, managers only. It
  moves every program forward in time (`POST /api/demo/advance`, `POST /api/demo/reset`).

---

## 4. Talking to the server

### 4.1 `api.ts`

[api.ts](../../frontend/src/api.ts) is the only module that calls `fetch` for the API (the landing page's warm-up call in
`handover.ts` is the one exception).

- `send()` adds the sign-in header: `X-Demo-User` in demo mode, otherwise `Authorization: Bearer <token>`. The token
  comes from `tokenSource`, which Firebase caches and refreshes near expiry.
- `api<T>()` calls `send()`. On a 401 outside demo mode it asks for a forced token refresh and retries **once**. A
  non-2xx answer throws an `ApiError`.
- `toError()` reads the server's `detail`. It understands three shapes: a plain string (if it looks like `snake_case`
  it is also the `code`), a list (FastAPI's 422 validation errors, joined into one line), and an object
  `{code, message, …}`. It also keeps the request id.
- `errorMessage()` is the one place that turns any thrown thing into a sentence for a person: 401 says the session
  expired, 413 names the size limit, 429 says to wait, a `TypeError` says the server could not be reached, and Firebase
  `auth/*` codes map to plain sentences. A 5xx adds "(reference …)" so the person can quote it.
- `post`, `patch`, `del` are one-line wrappers. `download()` saves a file the API sends (a plain link would carry no
  sign-in header). `upload()` uses `XMLHttpRequest`, because `fetch` cannot report upload progress.
- `safeHref()` returns a link only if it is `http:` or `https:`. A `javascript:` URL from a forged payload becomes
  `null` and is not rendered as a link.

The file also holds the shared types (`Config`, `Me`, `Project`, `User`, `Citation`, `KTClip`, `Task`) and small date
helpers. `day()` reads `YYYY-MM-DD` as a local date so it never shifts by a day across time zones.

### 4.2 `live.ts`: the stream

[live.ts](../../frontend/src/live.ts) keeps one stream open per signed-in tab: `GET /api/live`. The design rule is in its
first comment and is worth repeating:

> What arrives is never data, only "look again".

An event is `{t: topic, p: projectId}`. A page that shows that topic reloads it through the normal API. This keeps
permissions in one place (the normal routes), and means a missed event costs a stale page, never a wrong one.

**Why `fetch` and not `EventSource`.** `EventSource` cannot send an `Authorization` header. `liveRequest()` in `api.ts`
uses `fetch` with the same headers as every call, and `readEvents()` parses the Server-Sent Events text by hand: split on
blank lines, read `event:` and `data:` lines.

**The loop.** `loop()` runs while `running` is true:

1. Open the stream. On success the status is `live`.
2. If the stream had been lost for more than 2.5 s, emit `{t: "*"}`. Every subscriber reloads, because what changed
   meanwhile is unknown.
3. Read until the stream ends. The server ends every stream after 15 minutes (`STREAM_S` in
   [stream.py](../../backend/app/api/stream.py)) so the next one carries a fresh token. That is a normal end: reconnect
   at once, no backoff.
4. On an error: status `offline`, wait `backoff` plus up to 0.5 s of jitter, double the backoff up to 30 s.

**Hidden tabs let go.** After `HIDDEN_MS = 5000` in a hidden tab the stream is aborted, and reopened when the tab is
shown. The comment records the bug this fixed: over HTTP/1.1 a browser allows six connections per host across all tabs.
Each open stream holds one. With five or six background tabs, nothing was left for ordinary API calls and the app hung.

**Subscribing.** `useLive(topics, onChange, projectId?)` registers a callback. It ignores events for other projects and
debounces by 300 ms, so an agent writing a plan task by task causes one reload, not twenty.
`useLiveStatus()` feeds the "Reconnecting…" badge.

Topics, and who listens:

| Topic | Means | Listeners |
|---|---|---|
| `notifications` | A notification was written | The bell |
| `approvals` | An approval was created or decided | Approvals, hire rows |
| `tasks` | Onboardings, tasks, reviews | Tasks, Review, Settings, hire rows |
| `meetings` | KT sessions and their notes | KT sessions, KT planner, KT coverage, Code tour, Knowledge risk |
| `knowledge` | Links between KT moments and code | Code, Code tour, KT planner, Knowledge risk, KT coverage |
| `sources` | Repository and documents | Code, Code tour, Settings, the insight pages |
| `inbox` | Expert questions and answers | Ask, Expert inbox, KT coverage |
| `people`, `projects` | Members, invitations, projects | The shell (re-reads `/me`), Settings, the insight pages |
| `lifecycle` | Programs, milestones, blockers | Every `useApi()` call by default |

**One agent run, step by step.** `watchRun(runId, onStep)` opens a second, short stream:
`GET /api/live?topics=agent:<run_id>`. It is separate because steps *are* data for one view and must appear the moment
they arrive, while page-wide changes are batched. Its `ready` promise settles when the server confirms the
subscription, or after 700 ms, or at once if the stream cannot open. The question is asked either way.

### 4.3 Three ways a page loads data

| Pattern | Where | Behaviour |
|---|---|---|
| `useApi(path, topics)` | [lifecycle/kit.tsx](../../frontend/src/lifecycle/kit.tsx) | GET the path, again when a topic fires. Data stays on screen during a reload, so a live update never flashes a skeleton. A `seq` counter drops late answers. |
| `api()` in an effect plus `useLive()` | Most of `src/pages` | The same idea, written out by hand. |
| `getShared(path)` | [lifecycle/shared.ts](../../frontend/src/lifecycle/shared.ts) | Several parts of one screen read the same resource. A GET in flight is joined; an answer under 4 s old (`KEEP_MS`) is reused on first mount. Any write through `useAction()` clears the cache. `resetShared()` on sign-in and sign-out also discards reads still in flight, so the next account never sees the previous one's data. |

`useRefresh()` in [fx.tsx](../../frontend/src/fx.tsx) is a fallback for pages without a topic: reload on window focus,
on tab visibility, and every 30 s while visible.

---

## 5. The shared building blocks

| File | What is in it |
|---|---|
| [ui.tsx](../../frontend/src/ui.tsx) | `Logo` (the mark, with "arrive" and "busy" motions), `Splash`, `Booting`, `Note` (success or error, announced), `PageHead`, `toast()` and `Toaster`, `Loading`, `Icon`, `ErrorBoundary`. |
| [fx.tsx](../../frontend/src/fx.tsx) | Motion and state primitives: `Skeleton`, `CountUp`, `Ring`, `EmptyState`, `Kbd`, `Reveal` (fade in on scroll, one shared `IntersectionObserver`), `useRefresh`. All respect reduced motion. |
| [dataui.tsx](../../frontend/src/dataui.tsx) | Parts for the data pages: `Pill`, `StatusPill`, `DueChip`, `Avatar`, `CodeBlock`, `SectionHead`, `Glyph`. `Tone` is where colour meaning is enforced: `kt` is teal and means only "explained in a KT session"; `rec` is the one red. |
| [lifecycle/kit.tsx](../../frontend/src/lifecycle/kit.tsx) | Parts for the lifecycle pages: `useApi`, `useAction`, `useCrumb`, `hashOf()` (API path to hash route), `How` and `Why` disclosures, `LcHead`, `LoadError`, `LcSkeleton`, `Section`, `FigureCard`, `Progress`. |
| [accordion.tsx](../../frontend/src/accordion.tsx) | One section open at a time. Bodies mount on first open, so a long transcript costs nothing until asked for. The design rule "accordions rather than tabs on KT pages" is this component. |
| [pages/settings/kit.tsx](../../frontend/src/pages/settings/kit.tsx) | `SectionCard`, `RolePill`, `FileTile`, `GitHubMark` for Settings, New project and the GitHub callback. |
| [pages/chat-parts.tsx](../../frontend/src/pages/chat-parts.tsx) | `CopyButton`, `useAutoGrow` (textarea), `useReveal` (word-by-word reveal), and a small syntax highlighter (`highlight`) that colours with tokens. |
| [theme.ts](../../frontend/src/theme.ts), [theme-core.ts](../../frontend/src/theme-core.ts) | The light, dark or system choice. Split in two so the site can restore the saved theme without importing React hooks. |

There are three `Glyph` components and two `Avatar` components (in `dataui.tsx`, `chat-parts.tsx` and
`settings/kit.tsx`). They are separate icon sets for separate page families, not duplicates of one. Import the one that
belongs to the page family you are in.

**Theme.** The choice lives on `<html data-theme="light|dark">`; no attribute means "follow the system". `setTheme()`
writes `localStorage["ww-theme"]`, adds the class `theme-switching` for two frames (so hundreds of colour transitions do
not all animate at once), and fires an event that `useTheme()` listens to. A `storage` listener carries the change to
other tabs. While the site is showing (`html.is-site`), `apply()` does nothing: the site owns the theme then.

---

## 6. Every page

"Managers" below means managers and admins. "Experts" means experts, managers and admins. All paths are under `/api`.
Unless a row says otherwise, executives cannot open the page.

### 6.1 Lifecycle pages (`src/lifecycle`)

| Route | File | Shows | Calls | Who |
|---|---|---|---|---|
| `#/home` | [Home.tsx](../../frontend/src/lifecycle/Home.tsx) | A focus list, one key figure, then sections for the role. One view per role (`HireHomeView`, `ManagerHomeView`, `ExpertHomeView`, `AdminHomeView`, and the executive's). | `GET /home` | Everyone |
| `#/enrol` | [Enrol.tsx](../../frontend/src/lifecycle/Enrol.tsx) | One form that starts a program from a template. The Planner then drafts a plan into Approvals. | `POST /onboardings/enrol`; `GET /programs/templates`, `/teams` | Managers |
| `#/program/<id>?tab=` | [Program.tsx](../../frontend/src/lifecycle/Program.tsx) | One hire's program: header, timeline, then accordion sections. `?tab=` opens a section and follows the open one. | `GET /onboardings/<id>/…` per section | The hire, their mentor, managers |
| (sections) | [Basics.tsx](../../frontend/src/lifecycle/program/Basics.tsx), [Work.tsx](../../frontend/src/lifecycle/program/Work.tsx), [Reviews.tsx](../../frontend/src/lifecycle/program/Reviews.tsx) | Plan, milestones, access, pre-boarding; first task, progress, blockers, weekly summaries; check-ins, survey, completion. | `/onboardings/<id>/…`, `/blockers/<id>`, `/surveys` | As above, per section |
| `#/team` | [Team.tsx](../../frontend/src/lifecycle/Team.tsx) | Every hire with a compact timeline, state, blockers and mentor. Filters. | `GET /teams`, then per hire ([hires.ts](../../frontend/src/lifecycle/hires.ts)) | Managers |
| `#/reports` | [Reports.tsx](../../frontend/src/lifecycle/Reports.tsx) | Cohort figures, each with "not enough data" below its floor; survey aggregates; CSV export. | `GET /reports/onboarding`, `.csv` | Managers (project); admins and executives (organisation) |
| `#/agents` | [Agents.tsx](../../frontend/src/lifecycle/Agents.tsx), [AgentMap.tsx](../../frontend/src/lifecycle/AgentMap.tsx) | The diagram of the Supervisor and eight specialists, then one row per agent with its runs. | `GET /agents`, `/agents/runs`; `PATCH /agents/<key>`; `POST /agents/tick` | Managers; executives read-only; only admins switch modes |
| `#/templates?id=` | [Templates.tsx](../../frontend/src/lifecycle/Templates.tsx) | Program templates by track and level. Saving makes a new version. | `GET`/`PATCH /programs/templates` | Managers read, admins write |
| `#/handovers` | [Handovers.tsx](../../frontend/src/lifecycle/Handovers.tsx) | A plan for what only a leaver holds. | `GET`/`POST`/`PATCH /handovers` | Managers |
| `#/setup` | [Setup.tsx](../../frontend/src/lifecycle/Setup.tsx) | Resumable setup steps, and the one assumption reports rely on. | `GET`/`PATCH /setup` | Admins |
| `#/people` | [People.tsx](../../frontend/src/lifecycle/People.tsx) | Everyone in the organisation: role changes, deactivation. | `GET`/`PATCH /org/members` | Admins |
| `#/audit` | [Audit.tsx](../../frontend/src/lifecycle/Audit.tsx) | The audit log with filters, paging, CSV export. | `GET /audit`, `/audit.csv` | Admins |
| `#/me`, `#/me/<id>` | [Visibility.tsx](../../frontend/src/lifecycle/Visibility.tsx) | "What your manager sees": every data point others can read about the hire, and who reads it. | `GET /onboardings/<id>/visibility` | The hire; a manager with an id |
| `#/feedback` | [Feedback.tsx](../../frontend/src/lifecycle/Feedback.tsx) | Documentation feedback, grouped by what it is about. | `GET`/`PATCH /feedback` | Experts |
| `/welcome/<token>` | [Portal.tsx](../../frontend/src/lifecycle/portal/Portal.tsx) | Pre-boarding: welcome, people, documents to acknowledge, machine check, first week. Its own surface, no shell. | `GET /preboarding/<token>`; `POST …/acknowledge` | Anyone with the link |

Smaller parts: [Timeline.tsx](../../frontend/src/lifecycle/Timeline.tsx) (the day-0-to-end line),
[FeedbackButton.tsx](../../frontend/src/lifecycle/FeedbackButton.tsx) (the small flag beside a file or KT moment;
`POST /feedback`), [HireList.tsx](../../frontend/src/lifecycle/HireList.tsx),
[ReportParts.tsx](../../frontend/src/lifecycle/ReportParts.tsx), [icons.tsx](../../frontend/src/lifecycle/icons.tsx),
[types.ts](../../frontend/src/lifecycle/types.ts), [portal/week.ts](../../frontend/src/lifecycle/portal/week.ts).

Two of these pages are written to survive a server that lacks their API. `Agents` and `People` treat a 404 as "this
server does not have this yet" and say so, rather than showing an error.

### 6.2 Knowledge and work pages (`src/pages`)

| Route | File | Shows | Calls | Who |
|---|---|---|---|---|
| `#/chat?q=&message=` | [Chat.tsx](../../frontend/src/pages/Chat.tsx) | Ask the project. Section 7. | `/chat/history`, `/ask/free`, `/ask/route`, `/chat`, `/chat/<id>/feedback` | All but executives |
| `#/code?path=&line=` | [Code.tsx](../../frontend/src/pages/Code.tsx) | A file tree and a file. Each line shows whether, when and where a KT session explained it. | `/code/files`, `/code/file`, `/code/explanations`, `/coverage` | All but executives |
| `#/code-tour` | [CodeTour.tsx](../../frontend/src/pages/CodeTour.tsx) | A guided order through the code, stop by stop, with the KT clip for each. | `GET /tour` | All but executives |
| `#/meetings`, `#/meetings/<id>?t=` | [Meetings.tsx](../../frontend/src/pages/Meetings.tsx) | KT sessions: the list, scheduling, adding a recording; one session's video, notes and moments. Managers also see the transcript. | `/meetings…` (section 8) | All but executives |
| `#/kt-planner` | [KtPlanner.tsx](../../frontend/src/pages/KtPlanner.tsx) | Which sessions to record next, and what coverage they would add. | `GET /kt-plan`; `POST /kt-plan/schedule` | Experts |
| `#/knowledge-risk` | [KnowledgeRisk.tsx](../../frontend/src/pages/KnowledgeRisk.tsx) | Code only one person knows, and the handover time it implies. | `GET /knowledge-risk` | Managers |
| `#/dashboard` | [Dashboard.tsx](../../frontend/src/pages/Dashboard.tsx) | "KT coverage", module by module. About code, not people. | `GET /dashboard` | Managers |
| `#/tasks` | [Tasks.tsx](../../frontend/src/pages/Tasks.tsx) | Tasks in lanes. A hire can ask for a private review of a branch before opening a pull request. | `/tasks`, `PATCH /tasks/<id>`, `POST /tasks/<id>/presubmit-review` | All but executives |
| `#/reviews/<id>` | [Review.tsx](../../frontend/src/pages/Review.tsx) | The Reviewer's findings on one pull request. | `GET /reviews/<id>` | All but executives (the API narrows it) |
| `#/approvals/<id>?kind=&agent=` | [Approvals.tsx](../../frontend/src/pages/Approvals.tsx), [AgentApprovals.tsx](../../frontend/src/pages/AgentApprovals.tsx) | Everything waiting for a person's decision: task plans, reviews, KT follow-ups, re-recordings, doc fixes, template changes. | `GET /approvals`; `POST /approvals/<id>/decide` ([decide.tsx](../../frontend/src/pages/decide.tsx)) | Managers |
| `#/inbox` (also `#/experts`) | [Inbox.tsx](../../frontend/src/pages/Inbox.tsx) | Questions sent to this person, and verified answers. | `/experts/requests`, `…/answer`, `/verified-answers` | Experts |
| `#/documents/<id>?page=` | [Document.tsx](../../frontend/src/pages/Document.tsx) | A cited document, page by page, the cited page marked. | `GET /documents/<id>` | All but executives |
| `#/settings?tab=` | [Settings.tsx](../../frontend/src/pages/Settings.tsx) | Tabs: People, Repository, Documents, Onboarding, Integrations, Preferences, Organisation, Account. | `GET /projects/<id>`, then per tab | Experts get Documents, Integrations, Preferences, Account; managers all but Organisation; admins all. Executives get the Account page alone, and so do hires outside demo mode. |
| `#/projects/new` | [NewProject.tsx](../../frontend/src/pages/NewProject.tsx) | Create a project. | `POST /projects` | Managers |
| `/github/callback` | [GitHubCallback.tsx](../../frontend/src/pages/GitHubCallback.tsx) | Finishes a GitHub App installation. | `POST /github/callback` | Managers |

The settings tabs are one file each under [pages/settings/](../../frontend/src/pages/settings/): `People.tsx`,
`Repository.tsx`, `Documents.tsx`, `Onboarding.tsx`, `Integrations.tsx`, `Preferences.tsx`, `Organisation.tsx`,
`Account.tsx`, `Teams.tsx`. Shared KT parts are in [kt-parts.tsx](../../frontend/src/pages/kt-parts.tsx) (transcript,
follow-up sessions) and [schedule.tsx](../../frontend/src/pages/schedule.tsx) (the schedule dialog).

One detail in `GitHubCallback.tsx` protects against a real failure. GitHub's `code` works once. Every mount for the same
code shares one request, so a remount (a navigation click, a project switch) can never post it twice.

---

## 7. The Ask page and the trace

Files: [Chat.tsx](../../frontend/src/pages/Chat.tsx), [chat-parts.tsx](../../frontend/src/pages/chat-parts.tsx),
[Trace.tsx](../../frontend/src/lifecycle/Trace.tsx), [WhyThis.tsx](../../frontend/src/lifecycle/WhyThis.tsx),
[agentModel.ts](../../frontend/src/lifecycle/agentModel.ts). The server side of routing is described in [part 2](02-agents-and-answers.md) and in
[docs/specs/answer-routing.md](../specs/answer-routing.md).

### 7.1 What the page holds

- `items`: the conversation, from `GET /chat/history`. Each `Answer` has the question, the answer text, `citations`,
  `kt_clips`, and whether an expert has it.
- `routes`: what the routing call said about each turn (which tier answered, the trace, the "why"). The history does not
  keep this, so the page keeps it per tab in `sessionStorage["ww-routes:<project>"]`. A turn still shows its tier after a
  reload. Sign-out deletes these keys.
- `pending`: the question being answered right now, with its steps as they arrive.

### 7.2 Three endpoints, tried in order

The page works against servers of three ages, and finds out which it has by asking.

| Order | Call | Used when | Falls back when |
|---|---|---|---|
| 1 | `POST /ask/free` | A typed question. The Supervisor picks an agent. | 405, or a 404 that is not "turned off": `freeOk` becomes false and it is not tried again. |
| 2 | `POST /ask/route` | "Ask a person", escalation, or no `/ask/free`. The Guide's tiered routing. | A 404 whose message says "turned off": `routing` becomes false. |
| 3 | `POST /chat` | Routing is off on this server. | — |

`/ask/free` usually comes back handled by the Guide, and then its `result` is exactly an `/ask/route` answer. If another
agent took the request (the Coach, the Planner, the Scout), the page shows a `HandledCard` with that agent's proposal.
Links in agent output are followed only if they point inside this app (`handledOf`).

### 7.3 What each answer looks like

`Route.status` and `Route.tier` choose the card:

| Tier | Status | Card | Meaning |
|---|---|---|---|
| 1 | `answered` | `AnswerCard` | Written from the sources. Numbered markers `[2]` link to the source they cite. If `writer` is `extractive`, the label says the sentences are quoted (no model wrote them). |
| 2 | `related` | `RelatedCard` | The same question was answered before. Shown without names. |
| 3, 4 | `routed` | `Routed` | Sent to the mentor, then (via "escalate") to the owner of the code. The person's answer appears here when they write it. |
| — | `offered`, `unrouted`, or a failed check | `NotEnough` | Not enough evidence. The page offers "Ask a person". |

`Sources` renders citations as chips. A code source opens `#/code?path=…&line=…`; a KT source opens
`#/meetings/<id>?t=<seconds>`; a document opens `#/documents/<id>?page=…`. KT sources are teal. Nothing else on the page
is.

Under a tier-1 answer, `CrewLine` prints one quiet line such as "3 searches · 21 sources: code 14, docs 4, KT 3 · cited
2". It comes from the `crew` block, which carries counts only. The server never sends the queries, and `crewOf()` would
not show them if it did.

### 7.4 The trace: "watch it think"

A **step** (`TraceStep`) is one thing an agent did: `think`, `tool`, `verify` or `final`, with text, the tool name,
sources and a duration.

- `Thinking` shows steps while the agent works. `usePaced()` reveals them at least `STEP_GAP = 200` ms apart, so a burst
  still reads as a sequence. When the answer lands, the list folds (210 ms) into one button: "Thought for 6 steps ·
  4.2 s". Opening it shows `TraceList`.
- `WhyThis` is the small "Why this?" disclosure beside every agent output across the app. It shows the agent's name, its
  rationale or the rules that fired, the trace, the sources as links, the confidence, and whether AI wrote the words.
  If the payload has a `run_id` but no steps, it loads them from `GET /agents/runs/<run_id>` when opened. The trace id
  is shown to admins only.
- `agentModel.ts` is the defensive layer between payloads and components. `normaliseStep()` accepts a step under several
  field names and returns `null` for anything that is not one. `explainOf()` and `crewOf()` do the same for their
  blocks. `AGENT_REGISTRY` names the nine agents; `ALIAS` maps old run names (`qa`, `ingestion`) onto them. The reason
  for all this care: the agent runtime is newer than the pages, and an older or looser server must never crash a page.

### Example B: one question, end to end

A new hire types "Why does validation return a list instead of raising?" and presses Enter.

1. `ask()` makes a run id with `crypto.randomUUID()` and sets `pending`. The live region says "Searching…".
2. `watchRun(runId, …)` opens `GET /api/live?topics=agent:<runId>`. **The stream is opened before the question is
   sent**, so no step can be missed. `ask()` waits for `ready` (at most 700 ms).
3. `POST /api/ask/free` goes out with `{project_id, question, run_id}`.
4. Steps arrive on the stream: "Planning the search", "Searching the sources", "Checking KT sessions", "Ranking the
   evidence". Each is normalised, merged by its number `n` (`mergeSteps`), and shown by `Thinking`.
5. The POST returns. `handled_by` is `guide`, so `result` is a route answer: `tier: 1`, `status: "answered"`, the text,
   three citations, one KT clip, a `crew` block, and its own `steps`.
6. If the steps arrived in one burst, the page waits up to 1.2 s so they can be seen one by one. Reduced motion skips
   the wait.
7. The answer's own `steps` are the record (they are privacy-filtered by the server). Streamed steps are used only when
   the answer carries none.
8. `remember()` stores the route under the message id in `sessionStorage`. `turnOf()` turns it into an `Answer`, which is
   appended to `items`. The stream is closed.
9. The turn keeps its React key (the run id), so the `Thinking` list folds into its summary in place. Because the turn
   was answered on this page (`fresh`), it gets the class `is-new` and the `AnswerCard` animates in. A turn loaded from
   history is simply shown. The live region says "Answered".
10. The hire clicks the teal chip. The address becomes `#/meetings/mtg-1?t=95`, and the KT session opens at that
    second.

Failure paths in this flow:

- **429**: the question is put back in the box with "Too many questions in a minute".
- **Any other error**: `errorMessage()` text is shown, and the question is put back.
- **The stream cannot open**: `ready` settles at once. The answer still arrives and still carries its steps.
- **Routed to a person**: the history is reloaded so the card names who has it. When that expert answers, the `inbox`
  topic fires and the answer appears without a reload.
- **A reload while a question is in flight**: `load()` returns early while `asking` is true, so a history fetch never
  races the answer that is about to be appended. The append itself skips a turn the history already brought in.

---

## 8. Uploading a recording

The code is `AddRecording` in [Meetings.tsx](../../frontend/src/pages/Meetings.tsx). The server side is
[uploads.py](../../backend/app/api/uploads.py).

**The problem.** A recording is often hundreds of megabytes. On Google Cloud a request through Cloud Run is capped at
32 MiB. So the file cannot pass through the API there.

**The answer.** Three calls, the same on Google Cloud and on a self-hosted machine. Only step 2's destination differs,
and the browser does not need to know which it is.

| Step | Call | What happens |
|---|---|---|
| 1 start | `POST /meetings/<id>/recording/upload` with `{filename, size, duration_s}` | The server answers with an `UploadTicket`: `{id, method, url, headers, expires_at, max_bytes}`. |
| 2 send | `<method> <url>` with exactly `<headers>` and the raw file as the body | Google Cloud: a `PUT` to a signed Cloud Storage URL. Self-hosted: a `POST` to this API with the ticket in `X-Upload-Token`. No sign-in header and no cookies: the signed URL or the ticket is the credential. |
| 3 complete | `POST /meetings/<id>/recording/complete` with `{upload_id}` | The server checks the file is there, is the size and type that were signed, and starts like a video. Then it emits `meeting.recording_ready` and the Note-taker begins. |

### Example C: a manager adds a 600 MB recording

1. The manager picks `kt-validation.mp4`. `chosen()` checks the extension (`mp4`, `webm`, `mov`, `mkv`) and that the
   file is not empty. Stage: `preparing`.
2. `videoLength()` loads the file into a hidden `<video>` to read its duration. Recordings made in a browser (WebM)
   often report no length until asked to seek to the end, so it tries that and waits up to 8 s.
3. Step 1 returns a ticket. Stage: `uploading`. A `beforeunload` warning is armed, because leaving the page would drop
   the upload.
4. `sendFile()` sends the file with `XMLHttpRequest` and reports progress to a bar. A Cancel button calls `xhr.abort()`.
5. Stage: `checking`. Step 3 returns. A toast says "Recording added. Notes are on the way."
6. `onDone()` reloads the list. Later the `meetings` topic fires when the notes are ready, and the page updates itself.

Two retries are built in, each exactly once:

- Step 2 answers **403**: the link expired before the upload began (a sleeping laptop). The page gets a fresh ticket and
  sends again.
- Step 3 answers with code **`upload_missing`**: the file had not landed. The page sends it again, then completes again.

A file already stored (412 from Cloud Storage, 409 from the API) counts as sent. Every other failure becomes a sentence
that says what to do: too large, wrong type, storage not answering, already has a recording.

**Import from Google Drive.** For a session booked on Google Calendar, Meet attaches the recording to the event.
`POST /meetings/<id>/recording/import` fetches it. The page maps `no_recording_yet` and `import_in_progress` to plain
messages.

Four other files make this flow possible, and all four must agree:

- [nginx.conf.template](../../frontend/nginx.conf.template): the location for `/api/meetings/<id>/recording` allows
  2100 MB, turns request buffering off and waits 600 s. Every other `/api/` path allows 26 MB.
- The CSP's `connect-src` includes `https://storage.googleapis.com`, or the browser would refuse step 2 on Google Cloud.
- [data.tf](../../infra/terraform/data.tf): the bucket's CORS rule allows `PUT` from the web origins with the headers
  the signed URL needs. Its lifecycle rule deletes `recordings/` after 30 days.
- [config.py](../../backend/app/config.py): `max_recording_mb = 2048`. `START_S` in `uploads.py` gives 15 minutes to
  begin step 2.

---

## 9. The design system

### 9.1 Tokens

[styles.css](../../frontend/src/styles.css) defines every colour, radius, shadow and font as a CSS variable on `:root`,
then redefines the colours for dark under `:root[data-theme="dark"]` and again inside
`@media (prefers-color-scheme: dark)` for "follow the system". Every other stylesheet uses the variables. That is why
each stylesheet's first comment says "Tokens only, so light and dark both work".

The tokens that carry rules:

| Token | Value (light) | Rule |
|---|---|---|
| `--ink`, `--ink-2`, `--ink-soft`, `--ink-muted` | `#0a0a0a` down to `#707070` | Text. The comments record each contrast ratio; `--ink-muted` is the quietest that still passes WCAG AA. |
| `--ink-faint` | `#a1a1a1` | Hairlines and dots only. Too light for text. |
| `--kt` | `#0c7c72` (dark: `#2dd4bf`) | **Explained in a KT session.** Nothing else. |
| `--rec` | `#d92d20` | The one red: overdue, critical, failed. |
| `--warn`, `--info` | `var(--ink-2)` | Kept as names, but they resolve to ink. "Needs a look" and "in progress" are shown by weight, outline or shape, not colour. |
| `--rail*` | near-black | The rail is dark in both themes. |
| `--page-w` | `1080px` | One content width for every routed page. |
| `--ui`, `--code` | Geist, Geist Mono | Both served from `/fonts/` on this origin. No third party sees a visitor. |

If you add a colour, you are almost certainly breaking the first design rule. Add an ink weight instead.

### 9.2 Stylesheets and who loads them

A stylesheet is imported by the module that uses it, so it travels in that module's chunk.

| Stylesheet | Imported by | Class prefix |
|---|---|---|
| [styles.css](../../frontend/src/styles.css), [shell.css](../../frontend/src/styles/shell.css) | `AppEntry` | global, shell |
| [data.css](../../frontend/src/styles/data.css) | `dataui.tsx` | `dp-` |
| [lifecycle.css](../../frontend/src/styles/lifecycle.css) | `lifecycle/kit.tsx` | `lc-` |
| [chat.css](../../frontend/src/styles/chat.css) | `chat-parts.tsx`, so Ask, Code and Inbox | scoped under `.ask`, `.cv`, `.ib` |
| [trace.css](../../frontend/src/styles/trace.css) | `Trace.tsx` | `tk-` |
| [agents.css](../../frontend/src/styles/agents.css) | `Agents.tsx`, `WhyThis.tsx`, `AgentApprovals.tsx` | `ag-`, `ap-` |
| [planner.css](../../frontend/src/styles/planner.css), [risk.css](../../frontend/src/styles/risk.css), [tour.css](../../frontend/src/styles/tour.css) | their pages | `ktp-`, `rk-`, `ct-` |
| [entry.css](../../frontend/src/styles/entry.css), [signin.css](../../frontend/src/styles/signin.css) | sign-in, Settings | `en-` |
| [portal.css](../../frontend/src/styles/portal.css) | the pre-boarding page | no teal at all |
| [fx.css](../../frontend/src/styles/fx.css), [accordion.css](../../frontend/src/styles/accordion.css), [palette.css](../../frontend/src/styles/palette.css), [kt.css](../../frontend/src/styles/kt.css), [schedule.css](../../frontend/src/styles/schedule.css) | their components | |
| [site.css](../../frontend/src/site/site.css), [cinematic.css](../../frontend/src/site/cinematic/cinematic.css) | the site | `s-`, `w-`, `u-`, all under `.site` |

The site does **not** load `styles.css`. That is why `Logo` in `ui.tsx` sets its colour as an SVG attribute, not through
a stylesheet rule. The reverse case exists too: after "Log in" then Back, the app's stylesheet is still in the page
while the site shows. Every site rule is scoped under `.site` so the two cannot fight.

### 9.3 Motion

- The house rule is to animate `transform`, `opacity` and `clip-path`: properties the browser can animate without
  recomputing layout.
- The last line of `styles.css` is the global switch: under `prefers-reduced-motion: reduce`, every animation and
  transition is turned off with `!important`. Individual stylesheets also give reduced-motion end states where an
  element would otherwise stay hidden (`.reveal`, the skeleton shimmer).
- JavaScript checks the same preference before pacing or smooth scrolling (`reducedMotion()` in `fx.tsx` and
  `surface.ts`).
- Two easings are used almost everywhere: `--ease` and `--ease-out`.

### 9.4 Voice

[docs/VOICE.md](../VOICE.md) governs every string: one title and at most one helper line, buttons that start with a
verb, numbers over adjectives, designed empty and error states, sentence case, British spelling. `EmptyState`,
`LoadError` and `LcSkeleton` are the components that make those three states cheap to do properly.

---

## 10. The landing page

Files: [Site.tsx](../../frontend/src/site/Site.tsx), [Nav.tsx](../../frontend/src/site/Nav.tsx),
[Footer.tsx](../../frontend/src/site/Footer.tsx), [handover.ts](../../frontend/src/site/handover.ts),
[clock.ts](../../frontend/src/site/clock.ts), and the folder [cinematic/](../../frontend/src/site/cinematic/).

### 10.1 `Site.tsx`: the public site

`Site` renders `/` (the landing page), `/about`, and a 404 for any other path. While it is mounted, `<html>` carries:

| Class or attribute | Meaning |
|---|---|
| `is-site` | The site is showing. `theme-core.ts` leaves the theme alone. |
| `data-theme="light"` | About and the 404 are paper, whatever the saved choice. |
| `s-dark` | The landing page: dark from the first paint. |
| `motion` | Set unless the visitor prefers reduced motion. Hidden "before" states of entrances exist only under it, so turning on Reduce Motion mid-visit shows final frames at once. |
| `s-night` | Set by `setNight()` in `clock.ts` while a dark section is under the nav, so the nav inverts. |

Unmounting undoes all of it and calls `applySavedTheme()`, so the app gets its own theme back.

The site restores scroll itself: it writes `history.state.y` before leaving (`saveScroll`) and reads it in
`restorePosition()`. It sets `document.title`, and adds `<meta name="robots" content="noindex">` on unknown paths so a
mistyped link is never indexed.

The landing page is `CinematicLandingPage` followed by two ordinary sections, `AgentsBand` and `Security`, then the
`Finale` and `Footer`.

### 10.2 How scroll drives the film

The landing page is a film that the reader moves through by scrolling. The idea in one sentence: **the page scrolls
natively, and the film's position is a pure function of where the page is.** Nothing intercepts the wheel, nothing
smooths, nothing snaps.

**The layout** ([CinematicLandingPage.tsx](../../frontend/src/site/cinematic/CinematicLandingPage.tsx),
[cinematic.css](../../frontend/src/site/cinematic/cinematic.css)):

- `.s-view` is one screen tall and `position: sticky`. It holds the footage, the words, the product world, the chapter
  rail and the scroll cue. It stays put while the page scrolls under it.
- `.s-flow` holds one empty `.s-spacer` per section. Each spacer's height is `--span × 70svh`, where `--span` is the
  section's length in film seconds. The spacers are what make the page tall. They are also the in-page anchors
  (`#product`, `#mentor`, …).

So 70 % of a screen of scrolling is one film second. The film ends at `FILM_END = 33.7`, which makes the page about 24
screens long, plus one.

**The edit** ([film.ts](../../frontend/src/site/cinematic/film.ts)) is data, not code:

| Constant | What it is |
|---|---|
| `SHOTS` | Nine clips. Each has when it is on screen (`u0` to `u1`, in `F`), its dissolve (`fade`), its camera move (scale `s0`→`s1`, drift `d0`→`d1` about `origin`), where the clip starts (`c0`), and whether it is full-bleed footage (`stage`) or a screen inside the 3D world (`world`). |
| `SECTIONS` | Seven sections with the `F` each starts at. They drive the spacers, the nav's folio and the chapter rail. |
| `BEATS` | Ten rest points: where a composition holds and whose caption is up. |
| `CAPTION_RANGES` | When each caption is on screen. |
| `LEAKS` | When a light leak blooms over a dissolve. |
| `ZOOM_IN`, `ZOOM_OPEN` | The match cut: the camera pushes into a monitor, and the product world grows out of its screen. |
| `variant()` | Which file to load: a 9:16 cut for upright phones, 960 px for small or slow screens, else 1920. |

**The 3D set** ([geometry.ts](../../frontend/src/site/cinematic/geometry.ts)): where each product panel floats
(`x, y, z`, turn `ry`, width), when it emerges, the camera's keyframes, and the teal threads that tie a recorded moment
to the code it explains. `DESKTOP` is three stations the camera travels between. `PHONE` (under 900 px) is one column
the camera dollies through.

**The controller** ([ScrollStoryController.ts](../../frontend/src/site/cinematic/ScrollStoryController.ts)) is
`runStory()`. It is plain DOM code, not React: React renders the structure once, and the controller writes styles on it
every frame.

1. `measure()` runs on resize and once fonts settle, never per frame. It reads the track's top, the pixels of scroll per
   film second (`S`, measured from the first spacer), panel sizes and anchor positions.
2. A passive `scroll` listener does one thing: `kick()`, which schedules a frame if none is pending.
3. `frameNow()` computes `F = clamp((scrollY − top) / S, 0, FILM_END)` and calls four functions:
   - `load(F)`: give posters and video sources to the shots that are near.
   - `apply(F)`: for each shot, its opacity and camera transform; the light leak; the cover's words leaving;
     `zoomWorld(F)`; and `applyWorld(F)`, which moves the 3D camera along a smooth curve through its keyframes
     (`hermite`) and places each panel.
   - `words_(F)`: turn captions on and off.
   - `chrome(F)`: the chapter rail and the scroll cue.
4. `motion()` adds the only time-based touch: a smoothed velocity lifts the caption a few pixels and lets the film grain
   breathe while the page moves.
5. The loop requests another frame only while something is still moving. A visitor resting on the page costs nothing.

Why it stays smooth:

- **No layout is read per frame.** All measuring is in `measure()`.
- **Each frame writes only `transform`, `opacity` and `clip-path`**, and only when the value changed. Every writer
  remembers its last string (`v.last`, `lastZoom`, `lastCo`) and skips the write if it is the same.
- **The scroll never sets a video's time.** Seeking a video on every scroll event is what makes scroll-driven video
  stutter. Here each clip plays at its own speed while its shot is on screen and pauses when it is off
  ([SceneVideo.ts](../../frontend/src/site/cinematic/SceneVideo.ts)). The scroll moves the *composition*; the footage
  runs by itself.
- **Decoders are set up while the page is still.** Giving a `<video>` its source costs main-thread time. `SceneVideo`
  waits for a quiet moment (`quietAt`, pushed 450 ms ahead on every scroll event), at most 2.5 s for a clip ahead and
  250 ms for one already on screen.
- **The product world is warmed.** At the first quiet moment on the cover it is drawn for two frames at opacity 0.001,
  so its layers, images and text are rastered long before the push into the monitor.
- **Posters cover every failure.** Each shot has an `<img>` under its `<video>`. A decode error, blocked autoplay (an
  iPhone in Low Power Mode) or a slow network leaves the poster, never a hole.

**Long jumps.** Clicking the chapter rail or a far anchor would scroll through many screens of film. If the distance is
more than `JUMP = 2.5` screens, `cut()` fades a black layer in (0.22 s), jumps, and fades it out (0.45 s). Shorter
distances use the browser's own smooth scroll. `setScroller()` in `handover.ts` lets every in-page link on the site use
the same rule.

**Reduced motion and Save-Data.** No video is created at all. The film cross-fades between poster stills, and the world
fades in instead of growing out of the monitor.

**Accessibility.** The footage is `aria-hidden`. Every word of the story is real text in the DOM, in reading order.
Moving keyboard focus into a caption scrolls the film to that caption's beat.

### 10.3 Leaving the site

[handover.ts](../../frontend/src/site/handover.ts) handles links out of the site.

- `prefetch` is spread on every link into the app. On pointer enter, focus or touch start it imports the app's chunk
  and, outside demo mode, the Firebase module. By the time of the click, the code is usually there.
- `toApp()` keeps the tab (no reload, no second intro) and saves the scroll position for Back. Where the browser has
  View Transitions, the finale's black panel morphs into the sign-in page's black brand panel: both carry the
  transition name `ww-ink`. The old frame is held until the next page has drawn, for at most 900 ms.
- If the app's chunk failed to fetch (a deploy replaced it), `toApp()` does a plain page load instead.

[clock.ts](../../frontend/src/site/clock.ts) is a second, smaller frame loop for the site's other scroll-driven scenes
(About's fading call, the finale). Each scene has a `read()` and a `write()`; a frame runs every read first, then every
write, so no frame forces a second layout.

---

## 11. Crash reports

[report.ts](../../frontend/src/report.ts) catches what nothing else caught: render crashes (from the two error
boundaries), uncaught errors, and unhandled promise rejections.

- Each gets a six-character reference id, shown to the person.
- A report goes to `POST /api/client-errors` with `navigator.sendBeacon`, which works even while the page unloads.
- **It is redacted on purpose.** It carries the error name and message, the first lines of the stack, the page without
  its query (queries can carry tokens or search text), and the build. Nothing typed on the page is sent.
- At most five reports per page load (`MAX`), and each distinct error once.
- Chunk failures and `AbortError` are not reported: one is a deploy, the other is the app cancelling its own request.

The server side is tested in [test_release_client_errors.py](../../backend/tests/test_release_client_errors.py): no
sign-in is needed (a beacon carries none), and the route is rate-limited.

---

## 12. The web server and its CSP

[nginx.conf.template](../../frontend/nginx.conf.template) is the whole web server. The nginx image fills in four
variables at start: `PORT`, `API_URL`, `WEB_HOSTS` and `NGINX_LOCAL_RESOLVERS`.

### 12.1 What it does before any request is routed

- **Redacts the access log.** Pre-boarding links, signed file links and OAuth callbacks carry a credential in the URL.
  A `map` replaces the token with `[redacted]`, and the log format records no Referer.
- **Checks the host name.** `WEB_HOSTS` is a regular expression. A request for any other host gets `421`. The image's
  default is "any" (Cloud Run sees several names). The self-hosted release narrows it to `PUBLIC_URL`'s host and
  localhost, which blocks DNS rebinding: a page on another name cannot reach the API through this port.
- **Finds the caller's real address.** It takes the *last* entry of `X-Forwarded-For`, the one the proxy in front
  appended. Entries further left were written by the client and are dropped, so a made-up header can neither dodge a
  rate limit nor spend someone else's.
- **Looks up the API's address per request** (cached 10 s), not once at start. A restarted API container comes back on
  a new address, and nginx must follow it.

### 12.2 The Content-Security-Policy

| Directive | Allows | Why |
|---|---|---|
| `default-src` | `'self'` | Everything not listed below comes from this origin only. |
| `script-src` | `'self'`, Google sign-in and reCAPTCHA hosts | No `'unsafe-inline'`, no `'unsafe-eval'`. This is why the boot scripts are files. |
| `style-src` | `'self' 'unsafe-inline'` | The intro's `<style>` block, and style attributes the sign-in SDK sets. |
| `font-src` | `'self'` | Geist is served from `/fonts/`. |
| `img-src` | `'self' data: blob:` | `data:` for the intro's grain. |
| `connect-src` | `'self'`, Firebase Auth, `storage.googleapis.com` | The last is the recording upload (section 8). |
| `frame-src` | Firebase's auth iframe, Google sign-in, reCAPTCHA | A custom auth domain must be added here. |
| `media-src` | `'self' blob:`, `storage.googleapis.com` | KT recordings. |
| `worker-src` | `'self'` | The app starts no workers. |
| `object-src`, `base-uri`, `frame-ancestors` | `'none'` | No plugins, no `<base>` tricks, no framing. |

It also sends HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY`, a Referrer-Policy and a Permissions-Policy that
turns off the camera, microphone, geolocation and more.

One nginx trap is handled explicitly: a `location` with its own `add_header` drops the server-level ones. So
`/index.html`, `/assets/` and the media locations repeat the headers they need.

### 12.3 Locations

| Path | Behaviour |
|---|---|
| `/health` | Answers `ok` itself (liveness of this container). |
| `/health/ready` | Forwarded to the API, so one uptime check covers both. |
| `/api/` | Forwarded. 120 s timeout, 26 MB body. |
| `/api/live` | Forwarded with `proxy_buffering off` and a 1 h read timeout. Buffering would hold events back. |
| `/api/meetings/<id>/recording` | 2100 MB, unbuffered, 600 s. |
| `/webhooks/` | Forwarded (GitHub and calendar providers). |
| `/assets/` | Cached for a year, `immutable`. Safe because every file name contains a hash. |
| `/media/`, `/brand/` | Cached for a day. These names do not change across deploys. |
| `/index.html` | `no-cache`. A cached shell would point at asset names a deploy has removed. |
| `/`, `/about`, `/login`, `/signup`, `/github/callback`, `/app…`, `/welcome/<token>` | The shell. |
| anything else | The shell **with status 404**, so the site can show its 404 page but nothing is indexed as a real page. |

[frontend/Dockerfile](../../frontend/Dockerfile) builds in `node:22-slim`, writes a `.gz` copy of every text file over
1 kB (nginx serves those as they are, `gzip_static`), and runs in `nginx:1.30-alpine` as the unprivileged `nginx` user.
Both base images are pinned by digest.

---

## 13. Operations

### 13.1 Three modes, two images

The API reads `MODE` and behaves accordingly ([part 1](01-backend-and-data.md) covers `config.py`). From an operator's view:

| Mode | Data | Sign-in | Model | Background work | Start |
|---|---|---|---|---|---|
| `demo` | In memory, reset on restart | A demo account, `X-Demo-User` | Offline | In the API process | `docker compose up`, or uvicorn and `npm run dev` |
| `self_hosted` | Postgres in Docker, files on disk | Firebase | Gemini by API key | A local queue inside the API process | `./scripts/selfhost.sh` |
| `gcp` | Cloud SQL, Cloud Storage | Identity Platform | Vertex AI | Pub/Sub and three GKE workers | Cloud Build |

There are two images, and every mode uses the same two.

[backend/Dockerfile](../../backend/Dockerfile) builds one image for five jobs. Only the command differs: the API
(`uvicorn app.main:app`), the migrator (`python -m app.migrate`), and three workers (`python -m workers.worker` with
`WORKER_QUEUE` set). Things to know:

- Dependencies are installed from `requirements.lock` with `--no-deps`, then `pip check` proves the lock is complete.
  Nothing is resolved at build time.
- The final image has no `pip`. Nothing can be installed at run time.
- It runs as user `10001`, never root.
- One uvicorn worker per container. Cloud Run scales instances, not processes.
- `--proxy-headers` trusts `X-Forwarded-For` only from allowed addresses. The comment records the bug: with `*`, the
  leftmost entry (which the client writes) became the address that rate limits charged.

### 13.2 `docker-compose.yml`: the demo

There is one compose file, [docker-compose.yml](../../docker-compose.yml). It is for the demo and for local development
only. Every port is bound to `127.0.0.1`.

- `api`: the backend image, `MODE=demo` by default.
- `web`: the frontend image on `localhost:5173`, with `API_URL=http://api:8080`.
- `db`: Postgres with pgvector, behind the profile `db`. It starts only when asked for, and exists to run the Postgres
  tests.

The comment on the API's port is a safety rule, not decoration: demo mode trusts the `X-Demo-User` header, so it must
never be reachable from another machine.

### 13.3 `scripts/selfhost.sh`: one machine, for real use

[selfhost.sh](../../scripts/selfhost.sh) runs the real app on one machine. It begins with `umask 077`, so everything it
writes (settings, backups, recordings, keys) is readable by its owner only. Settings live in `backend/selfhost.env`.

| Command | What it does |
|---|---|
| `setup` | Writes `backend/selfhost.env` from the example with two fresh random database passwords (mode 600), and starts Postgres. Run once. It keeps an existing file. |
| `release` | Builds both images, starts Postgres, applies migrations, runs the API and web containers, waits until ready. Run again to update. |
| `start` | Development: Postgres and migrations, then the API from `backend/.venv` and the Vite dev server. Localhost only. |
| `status`, `logs`, `stop` | What they say. `stop` keeps the data. |
| `backup` | One owner-only `.tar` with the database, the files, the keys and the settings. |
| `restore <file> [--replace]` | Puts a backup back. |
| `check` | Tests each integration against its real service and prints PASS or FAIL per line, never a secret. |
| `migrate`, `api` | The pieces of `start`, separately. |

**What `release` does, and why each step is there:**

1. `release_checks`. `PUBLIC_URL` must be set and must be `https://` (or localhost): invitation links and sign-in
   return addresses are built from it, and sign-in tokens must not cross a network unencrypted. The Firebase project
   and key must be set. The auth emulator setting is refused. `web_hosts_re` builds the host-name pattern for nginx.
2. `build_images`: `ww-api:selfhost` and `ww-web:selfhost`.
3. `db_up`: creates or starts `ww-db` (`pgvector/pgvector:pg16`, bound to `127.0.0.1:5433`, data in the volume
   `ww-db-data`). It waits for Postgres **over TCP**: a new container first runs a temporary server on its socket only,
   and that one must not be mistaken for the real one. It then re-sets the owner's password from the settings file, on
   stdin so it never appears in the process list.
4. `migrate_image`: runs `python -m app.migrate` in the new API image as the owner role.
5. `run_api`: checks that Docker can really share the data folder (it writes a marker file and looks for it from inside
   a container), then starts `ww-api`. Every name in the settings file is passed by name with `-e NAME`, so no value
   appears on a command line. The two database passwords are left out: the API connects as the limited role `ww_app`.
   The container runs as the owner of the data folder, never root. Flags: `--restart unless-stopped`, a health check,
   log rotation.
6. `run_web`: starts `ww-web` on `127.0.0.1:8000` with `API_URL=http://ww-api:8080` and the host pattern.
7. `wait_ready`: polls `/health/ready` through the web container for up to 90 s. On failure it prints the API's last 40
   log lines.

The web app listens on localhost only. An HTTPS reverse proxy in front is the operator's job
([docs/ADMIN_GUIDE.md](../ADMIN_GUIDE.md), "Self-hosted release").

**Backup.** `backup()` writes into a temporary folder, then renames, so a half-written backup never has the final name.

1. `pg_dump` of the database, gzipped. It then checks the dump's last lines for "PostgreSQL database dump complete". A
   truncated dump fails the backup instead of becoming a backup that cannot be restored.
2. The data folder: documents, recordings, `.token-key` (it decrypts stored Google tokens) and `.signing-key`.
3. The settings file.
4. A `MANIFEST` (format version, time, migration level) and `SHA256SUMS`.

The backup holds every organisation's data and every key. The script says so when it finishes.

**Restore.** `restore()` verifies the checksums first. It refuses to touch a database that already has tables unless
`--replace` is given, and with `--replace` it takes a backup of the current data before dropping anything. It stops the
API and web containers while data is replaced. Three details matter:

- `pg_dump` leaves roles out. The dump grants data to `ww_app` and gives the cross-organisation functions to
  `ww_definer`. So the restore creates both roles first. Without that, those functions would end up owned by the
  restoring superuser, which would bypass row-level security. After loading, it checks that every `SECURITY DEFINER`
  function is owned by `ww_definer` and fails if not.
- The settings file from a backup is read **as text** (`env_value`), never sourced. A backup file is not run.
- It warns if `TOKEN_ENCRYPTION_KEY` or `BLOB_SIGNING_KEY` differs from the backup's, because stored Google connections
  would no longer decrypt.

The existing data folder is moved aside to `…before-restore-<time>`, not deleted. Migrations then run, so a backup from
an older release is brought up to date.

[package.sh](../../scripts/package.sh) zips the repository for sharing. It takes only files git knows about and then
applies its own exclusion list on top (`.env`, `selfhost.env`, `*.pem`, keys, dumps, backups, Terraform state), whatever
`.gitignore` says. The finished zip is checked before the script reports success.

### 13.4 Google Cloud

Two things build the cloud deployment. Terraform creates what lasts (database, bucket, queues, cluster, accounts).
Cloud Build deploys what changes (the images). The full procedure is [docs/DEPLOY.md](../DEPLOY.md).

**Terraform**, one file per concern, in [infra/terraform/](../../infra/terraform/). One GCP project per environment
(`dev`, `prod`).

| File | Creates |
|---|---|
| [main.tf](../../infra/terraform/main.tf) | Providers, variables, and the list of Google APIs to enable. A 60 s `time_sleep` after enabling, because an API is not usable the instant it is switched on. The state lives in a GCS bucket; it holds generated passwords. |
| [iam.tf](../../infra/terraform/iam.tf) | One service account per workload: `ww-api`, `ww-worker`, `ww-migrate`, `ww-web`, `ww-keda`, `ww-scheduler`, `ww-build`, each with only the roles it needs. |
| [data.tf](../../infra/terraform/data.tf) | Cloud SQL (PostgreSQL 16; regional with failover in prod; point-in-time recovery; encrypted connections only), the database `welcomewave`, the owner user and generated passwords. The storage bucket: private, no versioning, `recordings/` deleted after 30 days, CORS for the upload. |
| [events.tf](../../infra/terraform/events.tf) | For each queue (`ingestion`, `meetings`, `reviews`): a topic, a subscription, a dead-letter topic and its subscription. Four Cloud Scheduler jobs that call the API: `outbox-flush` (every 2 min), `recordings-poll` (every 10 min), `lifecycle-tick` (hourly), `audit-purge` (daily). |
| [compute.tf](../../infra/terraform/compute.tf) | The Artifact Registry repository, the GKE Autopilot cluster for workers, and two Cloud Build triggers: push to `main` deploys dev, a tag `vX.Y.Z` deploys prod. |
| [secrets.tf](../../infra/terraform/secrets.tf) | Secret Manager entries. Database URLs and the token encryption key are generated. Third-party values are added once by hand ([set-secrets.sh](../../infra/scripts/set-secrets.sh)). The `readers` map grants access per secret, per workload. |
| [identity.tf](../../infra/terraform/identity.tf) | Identity Platform (sign-in) and the browser API key. |
| [lb.tf](../../infra/terraform/lb.tf) | Optional, when `domain` is set: a global HTTPS load balancer, a managed certificate, and Cloud Armor (600 requests a minute per address). |
| [observability.tf](../../infra/terraform/observability.tf) | One email channel; an uptime check; alerts for dead letters, server errors, queue backlog, worker errors and restarts, scheduler failures, unsent outbox events; a monthly budget. A logging exclusion drops request logs whose URL is a credential. |
| [outputs.tf](../../infra/terraform/outputs.tf) | The bucket, the database connection name, the cluster, the Firebase key, the load balancer's address. |

The `readers` map in `secrets.tf` is the clearest statement of least privilege in the repository. For example, only
`migrate` can read `database-url-owner`. The API cannot read the owner's credentials even if it is compromised.

**Workers.** [workers.yaml](../../infra/k8s/workers.yaml) defines three Deployments from the one backend image:
`meeting-worker`, `ingestion-worker`, `review-worker`. KEDA scales each on its Pub/Sub backlog, down to zero when idle.
The Deployments name no `replicas`: KEDA owns the count, and a number in the file would reset it on every deploy and
stop a meeting mid-analysis. Each pod reaches Cloud SQL through the Auth Proxy as a sidecar. KEDA itself is installed
once per cluster by [bootstrap-cluster.sh](../../infra/scripts/bootstrap-cluster.sh).

**Cloud Build.** [cloudbuild.yaml](../../cloudbuild.yaml) is the pipeline.

| Step | What it does |
|---|---|
| `stamp` | Records when this build started. |
| `postgres`, `backend-tests` | Starts a scratch Postgres, runs `ruff` and the full test suite including the Postgres tests. |
| `eval` | The retrieval gate (section 15). |
| `terraform-check` | `terraform fmt -check` and `validate`. Nothing is applied. |
| `image-backend`, `image-web` | Build both images. The web image build runs `tsc`, so a type error fails here. |
| `push` | Only after tests, eval, the Terraform check and both builds have passed. |
| `migrate` | Deploys and runs the Cloud Run job `ww-migrate` as the owner role. |
| `deploy-api`, `deploy-web` | Cloud Run services `ww-api` and `ww-web`. |
| `deploy-workers` | Substitutes placeholders in `workers.yaml`, `kubectl apply`, waits for rollout. |
| `smoke` | Health, readiness, `/api/config`, and that `/api/me` answers 401 without a sign-in. |

Decisions in that file that are easy to undo by accident:

- **`--update-env-vars`, not `--set-env-vars`.** `set` replaces the whole list and would silently reset anything an
  admin set on the service by hand (feature flags, rate limits).
- **`--timeout 3600` on both services.** Every signed-in tab holds a stream for up to 15 minutes. Cloud Run's default of
  5 minutes would cut every stream early.
- **`--session-affinity` on the API.** A person's stream and the requests that publish to it tend to reach the same
  instance.
- **The `ww_build` label.** Each deploy labels `ww-api` with its build's start time. Every later step first compares.
  If two builds run at once, the older one stops instead of deploying last.
- **`_INGRESS`.** Without a domain it is `all`. With one, Terraform sets `internal-and-cloud-load-balancing`, so the
  `run.app` addresses refuse the internet and nothing bypasses Cloud Armor. The smoke test then checks the opposite
  too: that the direct addresses do *not* answer.

### Example D: a merge to `main` reaches the dev environment

1. A push to `main` fires the trigger `ww-main-to-dev`. Cloud Build runs as `ww-build`.
2. In parallel: tests against a scratch Postgres, the eval, the Terraform check, and both image builds.
3. All pass. Both images are pushed, tagged with the commit's short SHA.
4. `ww-migrate` runs the new image's `app.migrate` against Cloud SQL as `ww_owner`. Any new numbered migration is
   applied.
5. `ww-api` is deployed with the new image, and the three workers are re-applied in parallel with it. Then `ww-web` is
   deployed, with `API_URL` set to the API's address.
6. The smoke test calls the live services.
7. A tab that was open throughout hits its next lazy chunk, finds the old file name gone, and `reloadOnce()` reloads it
   onto the new build (section 2.3). That is the reason `index.html` is never cached and `/assets/` always is.

---

## 14. CI

[ci.yml](../../.github/workflows/ci.yml) runs on every pull request and on pushes to `main`. Five jobs:

| Job | Proves |
|---|---|
| `backend-memory` | Installs the image's exact versions (`--no-deps` from the lock, `pip check`). `ruff`. All tests in memory. That the tests left nothing in `backend/`. The retrieval eval. |
| `backend-postgres` | The same tests with a real Postgres 16 and pgvector. It **fails if the Postgres tests were skipped**, by searching the output for the skip reason. |
| `frontend` | `tsc`, `vite build`, then that the build fits the CSP: no `eval(` or `new Function(` in any bundle, no `new Worker(`, no inline script or inline handler in `index.html`. |
| `dependency-audit` | `pip-audit` on the lock (any known vulnerability fails). Every npm package comes from `registry.npmjs.org`. `npm audit` on what ships to browsers: high or critical fails, unless the advisory is in the reviewed `accepted` list with its reason. |
| `docker` | Builds both images. The web image serves the CSP, HSTS and gzip, and does not run as root. The API image starts in demo mode and stores an uploaded document. Then a full self-hosted cycle: `setup`, `release`, `status`; an unknown `Host` gets 421; kill the API and confirm Docker restarts it; `backup`; delete the database, the files and the settings; `restore`; confirm the data, the file and the function owners; confirm a second restore is refused without `--replace`. |

Actions are pinned to a commit, base images to a digest. [dependabot.yml](../../.github/dependabot.yml) proposes
updates weekly for Docker images, actions, pip and npm, so the pins do not go stale. A person merges each one.

The project's rule is: a change is done when these checks pass locally.

---

## 15. The tests

### 15.1 What exists

There is **no front-end test runner**. The web app is held in place by the type checker, the build, the CSP checks in
CI, and back-end tests that read front-end files (below). The landing film exposes `data-beat` on its track so an
outside browser test can assert where the film is, but no such test lives in this repository.

All tests are in [backend/tests/](../../backend/tests/): 60 test files. A run on 2026-11-18 without a Postgres server
gave 1,104 passed and 76 skipped, in about two and a half minutes. The 76 are the Postgres tests.

### 15.2 How tests are set up

[conftest.py](../../backend/tests/conftest.py) does three things for every test:

- **Rate limits are off** (`RATE_LIMIT_ENABLED=false`, set before any module imports the app). Many tests act as one
  person many times a minute. [test_ratelimit.py](../../backend/tests/test_ratelimit.py) turns them on itself.
- **Nothing is written into `backend/`.** The `scratch_dirs` fixture points `DATA_DIR` and local blob storage at a
  temporary folder. CI checks afterwards that `.blobs` and `.data` do not exist.
- **The `world` fixture** builds a small in-memory project: a manager (Priya), a hire (Vijay), an expert (Ravi), a
  repository, one KT session, a `FakeLLM`, and the sample repository as the code source.

No test calls a real model. `FakeLLM` answers from scripts; the demo's offline model answers by rules.

### 15.3 The files, by theme

| Theme | Files | What they hold in place |
|---|---|---|
| The demo as one story | `test_golden_path.py`, `test_journeys.py`, `test_api.py` | Repository → KT session → notes → plan → question → pull request → review, end to end. |
| Agents | `test_agents.py`, `test_agents_model.py`, `test_agent_runtime.py`, `test_guide_agent.py`, `test_thinking_agents.py`, `test_release_routing.py` | The roster, the Supervisor's routing, the step budget and timeout, traces and their privacy, the guardrails code enforces whatever the model says. |
| Retrieval | `test_rag_core.py`, `test_rag_integration.py`, `test_offline_writer.py`, `test_services.py`, `test_embedding_limits.py`, `test_release_unseen_eval.py` | The research crew, the writer that only quotes, chunking, pacing under a provider's limits. |
| Security | `test_injection.py`, `test_permissions_matrix.py`, `test_release_security.py`, `test_release_privacy.py`, `test_hire_access.py`, `test_visibility.py`, `test_audit.py`, `test_ratelimit.py`, `test_firebase_tokens.py` | Planted instructions are treated as data. Every route against every role. A hire's data stays the hire's. The audit log. |
| Sign-in and people | `test_auth_flow.py`, `test_demo_login.py`, `test_members.py`, `test_org_data.py` | The 403 codes that `AuthGate` branches on (section 2.5). Organisation export and deletion remove everything. |
| Lifecycle | `test_lifecycle_api.py`, `test_lifecycle_rules.py`, `test_survey_floor.py`, `test_release_product.py` | Programs, check-ins, the survey's floor of five people, journeys that once had no way through. |
| Knowledge pages | `test_knowledge_core.py`, `test_knowledge_risk.py`, `test_kt_planner.py`, `test_kt_review.py`, `test_code_tour.py`, `test_scheduling.py` | The figures behind Knowledge risk, the KT planner, the Code tour; scheduling with Google answered by a mock transport. |
| Running it | `test_config.py`, `test_selfhost.py`, `test_release_selfhost.py`, `test_release_gcp.py`, `test_release_reliability.py`, `test_integration_status.py`, `test_mailing.py`, `test_release_client_errors.py` | Demo mode reads no env file. The local queue survives errors. The upload flow of section 8. Crash reports. |
| Regression rounds | `test_review_fixes.py`, `test_review_round.py`, `test_round3_api.py`, `test_round3_runtime.py`, `test_round3_store.py`, `test_api_production.py` | One test per defect found in each review round. |
| Postgres | `test_postgres.py`, `test_lifecycle_postgres.py`, `test_rag_postgres.py`, `test_org_data_postgres.py`, `test_chat_writer_postgres.py`, `test_release_gcp_postgres.py`, `test_release_security_postgres.py`, `test_journeys_store.py` | The same behaviour on a real database, as the limited role, under row-level security. |

**The test that guards this part of the guide** is
[test_release_selfhost.py](../../backend/tests/test_release_selfhost.py). It is a Python test about files that are not
Python. It parses `nginx.conf.template` and asserts the CSP allows only what the app uses, that the host check comes
before any location, and that the access log redacts credentials. It sources `selfhost.sh` in bash and tests its pure
functions (`web_hosts_re`, `release_checks`, `container_db_url`, `env_value`). It checks what `package.sh` refuses to
share, that images are pinned and unprivileged, and that CI still fails on vulnerabilities and lint. It starts no
container; CI's `docker` job does that part.

### 15.4 How the Postgres tests run

They need a scratch server and a superuser address in `TEST_PG_ADMIN_URL`. Without it they skip.

```bash
docker run -d --name ww-pg -e POSTGRES_USER=ww -e POSTGRES_PASSWORD=ww -p 55432:5432 pgvector/pgvector:pg16
cd backend && TEST_PG_ADMIN_URL=postgresql://ww:ww@localhost:55432/postgres .venv/bin/python -m pytest -q
```

Port 55432 keeps the scratch server clear of 5433, where a self-hosted database on the same machine listens.

**Never point `TEST_PG_ADMIN_URL` at a server that holds real data.** The fixture starts from a clean server: it drops
every database named `ww_it_*` or `ww_test`, and it drops the roles `ww_app`, `ww_definer` and `ww_owner` together with
everything they own. Those are the same role names a self-hosted installation uses.

The `db` fixture in [test_postgres.py](../../backend/tests/test_postgres.py) sets the database up **the way Cloud SQL
is**, which is the point of these tests:

- It creates a fresh database and an owner role that is `NOSUPERUSER`. Cloud SQL's users are not superusers, so a
  migration that quietly needs superuser rights fails here, not in production.
- It runs the real migrator and asserts every migration applied, in order.
- The application then connects as `ww_app`, which cannot bypass row-level security. A query that forgets an
  organisation filter returns nothing instead of another organisation's rows.

Other Postgres modules reuse this fixture. Several files (`test_round3_store.py`, `test_journeys_store.py`) run each
test twice, once on `MemoryStore` and once on Postgres, to prove the two adapters answer the same.

### 15.5 The eval gate

[run_eval.py](../../backend/eval/run_eval.py) is a gate, not a test: it measures quality and exits 1 when a number
drops. It is offline and deterministic (the demo's offline model, no key, no network, about 15 s).

```bash
cd backend && MODE=demo .venv/bin/python -m eval.run_eval
```

| Metric | Target | Measures |
|---|---|---|
| `golden_set index R@5` | 0.80 | The original gate: is the right file in the top five? |
| `demo crew R@5`, `MRR@10`, `nDCG@10` | 0.90, 0.86, 0.84 | The research crew on 57 questions about the demo project. |
| `self crew R@5`, `MRR@10`, `nDCG@10` | 0.75, 0.60, 0.56 | The same on 93 questions about Welcome Wave's own code and docs. |
| `demo e2e refusal (unanswerable)` | 0.97 | Does the Guide refuse what it cannot support? |
| `demo e2e answered (answerable)` | 0.62 | Does it answer what it can? This stops "refuse everything" from passing. |
| `golden sources missing` | at most 0 | Every expected source still exists in its corpus. |

Each target is the value measured when it was set, floored to two decimals, minus 0.03 (`MARGIN`). `--measure` prints
current values and the targets they would give. The rule in the file's own words: fix the case, not the metric. The
"self" numbers move a little whenever `backend/app` changes, because that code is the corpus; refresh those targets
after a large change, never to let a regression through.

[eval/README.md](../../backend/eval/README.md) describes the other evaluations, which never run in CI because they need
a real model or a download.

---

## 16. Constants that matter

| Constant | Where | Value | If it changes |
|---|---|---|---|
| `MIN_MS` | `boot.ts` | 4150 | Lower than 3750 cuts the three words off. It must follow the CSS timeline in `index.html`. |
| `FADE_MS` | `boot.ts` | 850 | Must be at least `REVEAL_MS` (820) in `boot-field.js`, or the intro is removed mid-reveal. |
| `STALL_MS`, `SKIP_STALL_MS` | `boot-words.js` | 12000, 3000 | Too low shows "Still loading…" on ordinary slow loads. |
| `COVER_FAILSAFE_MS` | `Site.tsx` | 12000 | The landing cover shows itself if the intro never reports leaving. |
| Workspace preload delay | `App.tsx` | 600 ms | Sooner competes with sign-in's own requests. |
| `preloadPages` quiet time | `lazyPage.ts` | 1000 ms since input | Lower lets chunk evaluation land mid-scroll. |
| `HIDDEN_MS` | `live.ts` | 5000 | Much higher brings back the six-connection hang with many tabs over HTTP/1.1. |
| Gap before reload-all | `live.ts` | 2500 ms | Lower reloads every page on each routine reconnect. |
| Backoff | `live.ts` | 1 s doubling to 30 s | A fixed short retry hammers a server that is down. |
| `useLive` debounce | `live.ts` | 300 ms | Lower turns an agent's burst of writes into a burst of reloads. |
| `STREAM_S` | `stream.py` | 15 min | Must stay under the ID token's one-hour life and under nginx's and Cloud Run's 1 h timeouts. |
| `watchRun` wait | `live.ts` | 700 ms | The longest a question is held back for the step stream. |
| `STEP_GAP` | `Trace.tsx` | 200 ms | Paces live steps. `Chat.tsx` uses it to compute its catch-up wait. |
| `KEEP_MS` | `shared.ts` | 4000 | How long a shared read is reused on first mount. |
| Scroll memory | `scroll.ts` | 60 entries, 3 s restore | |
| Recording body limit | nginx, `config.py` | 2100 MB, 2048 MB | nginx must stay above the API's limit, or nginx refuses first with a bare 413. |
| Ticket life | `uploads.py` | 15 min | Step 2 must *start* within it. |
| Film scale | `cinematic.css` | 70svh per film second | See the first note in section 18. |
| `FILM_END`, `SECTIONS`, `BEATS`, `SHOTS` | `film.ts` | | All in film seconds. `geometry.ts` uses the same clock; move a beat and its camera keyframe must move too. |
| `JUMP` | `ScrollStoryController.ts` | 2.5 screens | Above it, a jump cuts through black. |
| `quietAt` push | `ScrollStoryController.ts` | 450 ms | How long after a scroll event the page counts as moving. |
| `MAX` | `report.ts` | 5 | Crash reports per page load. |
| Eval `MARGIN` | `run_eval.py` | 0.03 | The slack under each measured value. |

---

## 17. Things that must stay in step

Each row is one rule written in more than one file. Change one, change all.

| Rule | Files |
|---|---|
| Which address belongs to which surface | [surface.ts](../../frontend/src/surface.ts) `surfaceFor`, [boot-theme.js](../../frontend/public/boot-theme.js), the shell locations in [nginx.conf.template](../../frontend/nginx.conf.template), `TITLES` in [Site.tsx](../../frontend/src/site/Site.tsx) |
| The intro's timings | [index.html](../../frontend/index.html) (CSS delays), [boot-field.js](../../frontend/public/boot-field.js) (its constants), [boot.ts](../../frontend/src/boot.ts) (`MIN_MS`, `FADE_MS`) |
| The intro's field colour | `--b-bg` in `index.html`, the `theme-color` values in `boot-theme.js` |
| A new page | The lazy import, `PAGE`, the route chain, the rail `groups` or the palette's `more` list in [Workspace.tsx](../../frontend/src/Workspace.tsx); then a row in [docs/PERMISSIONS.md](../PERMISSIONS.md) for its API routes, which `test_permissions_matrix.py` enforces |
| A new outside host the browser talks to | The CSP in `nginx.conf.template`, and the expected set in [test_release_selfhost.py](../../backend/tests/test_release_selfhost.py) |
| A new live topic | The publisher in the back end, the list in [live.ts](../../frontend/src/live.ts)'s header, and each page's `useLive` |
| A new `Config` flag | `GET /api/config` in the back end and the `Config` type in [api.ts](../../frontend/src/api.ts) |
| Film timing | [film.ts](../../frontend/src/site/cinematic/film.ts), [geometry.ts](../../frontend/src/site/cinematic/geometry.ts), and the `70svh` in [cinematic.css](../../frontend/src/site/cinematic/cinematic.css) |
| The recording limit | `client_max_body_size` in `nginx.conf.template`, `max_recording_mb` in [config.py](../../backend/app/config.py), the bucket CORS in [data.tf](../../infra/terraform/data.tf) |
| Python and Node versions | [backend/Dockerfile](../../backend/Dockerfile), [frontend/Dockerfile](../../frontend/Dockerfile), `PYTHON_VERSION` and `NODE_VERSION` in [ci.yml](../../.github/workflows/ci.yml), the images in [cloudbuild.yaml](../../cloudbuild.yaml) |
| Environment variables for the API on Google Cloud | `cloudbuild.yaml` (`--update-env-vars`, `--set-secrets`), [workers.yaml](../../infra/k8s/workers.yaml) (the workers' copy), [secrets.tf](../../infra/terraform/secrets.tf) (who may read what) |

---

## 18. Things noticed while reading

Small mismatches between a comment or a rule and the code. None is a bug in behaviour today.

1. **`SCROLL_PER_SECOND` is not used.** [film.ts](../../frontend/src/site/cinematic/film.ts) exports
   `SCROLL_PER_SECOND = 0.7`, but nothing imports it. The real value is the `70svh` in
   [cinematic.css](../../frontend/src/site/cinematic/cinematic.css), and the controller measures the result from the
   DOM. Changing the constant alone changes nothing. To change the film's scroll length, change the CSS.
2. **A comment about fonts is out of date.** The CSP comment in
   [nginx.conf.template](../../frontend/nginx.conf.template) mentions Google Fonts under `style-src`. The policy itself
   has `font-src 'self'`, and Geist is served from `/fonts/`. The policy is right; the comment is not.
3. **The motion rule has paint-only exceptions.** The project rule says motion animates only `transform`, `opacity` and
   `clip-path`. A few animations use other properties that also avoid layout: `stroke-dashoffset` (the intro's strokes,
   the progress ring), `background-position` (the skeleton shimmer, the slow-load sweep), and `filter: blur` (the
   intro's three words). Nothing found animates a layout property such as width or top.
4. **"Docker compose files" is one file.** Only [docker-compose.yml](../../docker-compose.yml) exists. The self-hosted
   release does not use compose; `selfhost.sh` runs `docker` directly.
5. **Role checks exist twice by design.** The chain in `Workspace.tsx` and the table in `docs/PERMISSIONS.md` are
   separate lists. Only the second is enforced by a test. A page added to the first without the second will draw, then
   fail to load.
6. **The web app has no automated tests of its own.** Its behaviour is checked by types, the build and the CSP greps.
   The flows in sections 2, 7 and 8 are the ones most worth a browser test if one is ever added; that would be a new
   development dependency and needs approval first.
