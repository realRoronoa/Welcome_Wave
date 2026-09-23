# Welcome Wave — Frontend Build Roadmap (for the coding agent)

## Read this first

Your job in this pass is **structure and logic only** — components, types,
state, props, data flow. **Do not write CSS, do not style anything, do not
create `.css` files.** A separate pass (or a separate agent) owns visual
design. If a component needs a class name for the design pass to hook into
later, add the class name to the JSX, but leave its appearance undefined.

Every component should render correctly with zero styling — plain browser
default fonts and spacing is the expected, acceptable output of this pass.

## Ground rules

1. **All data is hardcoded.** No `fetch()`, no backend calls, no `axios`.
   Everything comes from one file: `src/mockData.ts`.
2. **Types first, always.** Nothing gets written to `src/mockData.ts` or any
   component until `src/types.ts` defines the shape it needs.
3. **One direction of data flow.** `App.tsx` is the only file allowed to
   call `useState` for data that more than one component needs (the current
   user, the list of answers). Every other component receives that data as
   **props**, and receives a **setter function** as a prop if it needs to
   change that data. A component should never import mutable state directly
   from `mockData.ts` — only fixed reference data (like `ROADMAPS`) is safe
   to import directly, because it never changes at runtime.
4. **TypeScript strict mode is on.** After every step, run:
   ```
   cd frontend && npx tsc -b
   ```
   Zero errors before moving to the next step. Do not skip this.
5. **Existing folders, don't invent new ones.** The repo already has empty
   folders at `frontend/src/roadmap/`, `ask/`, `verifyQueue/`, `dashboard/`.
   Use them. Add `frontend/src/shared/` and `frontend/src/roleSelect/` only
   — those are the two new folders this roadmap needs.

## Build order and why

Build in this exact order. Each step only makes sense once the step before
it exists, because later components import from earlier ones.

---

### Step 1 — `src/types.ts`

**Purpose:** the shared contract every other file imports from.

Define:
- `Department` — union of `"sales" | "hr" | "engineering"`
- `FunctionalRole` — union of `"admin" | "domain_expert" | "manager" | "new_hire"`
- `User` — `id`, `name`, `email`, `department: Department`, `functionalRole: FunctionalRole`
- `RoadmapData` — `department`, `overview`, `readFirst: string[]`, `firstTask`
- `RoadmapProgress` — `completed: boolean`, `completedAt: string | null`
- `AnswerStatus` — union of `"ai_draft" | "verified" | "stale" | "not_found"`
- `Evidence` — `source`, `section`
- `Answer` — `id`, `question`, `department`, `status: AnswerStatus`, `text`,
  `evidence: Evidence[]`, optional `verifiedBy?`, `verifiedAt?`, `staleReason?`,
  and `askedCount: number`
- `AuditAction` — union of `"ASK" | "VERIFY_ANSWER" | "FLAG_STALE" | "TASK_COMPLETED" | "CONNECT_SOURCE"`
- `AuditEvent` — `id`, `actorId`, `action: AuditAction`, `timestamp`
- `ViewName` — union of `"roadmap" | "ask" | "verifyQueue" | "dashboard"`

**Definition of done:** file compiles with `npx tsc -b` (nothing imports it
yet, so this step can't fail on its own — just check for syntax errors).

---

### Step 2 — `src/mockData.ts`

**Purpose:** every hardcoded value the app needs, in one place.

Export:
- `USERS: User[]` — at least one person per functional role (new_hire,
  domain_expert, manager, admin), spread across at least two departments
- `ROADMAPS: Record<Department, RoadmapData>` — one entry per department
- `ANSWERS: Answer[]` — a mix of statuses: at least one `verified`, one
  `ai_draft`, one `stale`
- `TOPIC_KEYWORDS: Record<Department, { keywords: string[]; answerId: string }[]>`
  — used later by Ask to fake semantic search with keyword matching
- `AUDIT_EVENTS: AuditEvent[]` — a couple of seed events

**Definition of done:** `npx tsc -b` passes. Every array has at least the
minimum count noted above.

---

### Step 3 — `src/shared/TrustBadge.tsx`

**Purpose:** one component, reused by Ask, Verify Queue, and Dashboard —
this is why it goes in `shared/`, not inside any single feature folder.

**Props:** `{ status: AnswerStatus }`

**Behavior:** render different text depending on `status` — one branch per
value of `AnswerStatus`. No conditional styling logic needed, just make
sure all four states (`ai_draft`, `verified`, `stale`, `not_found`) render
distinguishable text content (e.g. "AI draft — unverified", "Verified",
"Needs re-verification", "Not found").

**Definition of done:** exported as default, accepts the prop, compiles.

---

### Step 4 — `src/shared/Sidebar.tsx`

**Purpose:** navigation that changes depending on who's logged in.

**Props:**
```
{
  user: User;
  view: ViewName;
  onNavigate: (view: ViewName) => void;
  onSwitchIdentity: () => void;
}
```

**Behavior:** define a lookup (e.g. `Record<ViewName, { label: string; roles: FunctionalRole[] }>`)
mapping each view to which functional roles can see it. Filter to only the
views `user.functionalRole` is allowed to see, render one button per
remaining view that calls `onNavigate(viewName)`, plus one button that
calls `onSwitchIdentity()`.

**Definition of done:** for each of the 4 mock users in Step 2, manually
trace which buttons should appear and confirm the filter logic produces
that list.

---

### Step 5 — `src/roleSelect/RoleSelect.tsx`

**Purpose:** stands in for login — pick a persona, become that person.

**Props:** `{ users: User[]; onSelect: (user: User) => void }`

**Behavior:** render one clickable item per user in `users`, showing at
least their name and department. Clicking calls `onSelect(user)`.

**Definition of done:** compiles, no internal state needed — this
component is a pure list-and-callback.

---

### Step 6 — `src/roadmap/RoadmapView.tsx`

**Purpose:** the new hire's landing screen.

**Props:** `{ user: User }`

**Behavior:**
- Look up `ROADMAPS[user.department]` to get this user's roadmap.
- Local `useState<RoadmapProgress>` starting at `{ completed: false, completedAt: null }`.
- Render `overview`, the `readFirst` list, and `firstTask`.
- A button that, when clicked, sets progress to
  `{ completed: true, completedAt: new Date().toISOString() }`.
- Once `completed` is `true`, replace the button with a completed message
  instead of leaving it clickable.

**Definition of done:** clicking the button flips the UI state and the
button disappears — test this by hand in the browser once styling exists,
but the state logic must work even unstyled.

---

### Step 7 — `src/ask/AskView.tsx`

**Purpose:** the trust loop's entry point.

**Props:**
```
{
  user: User;
  answers: Answer[];
  setAnswers: React.Dispatch<React.SetStateAction<Answer[]>>;
}
```

**Behavior:**
- Local state: a conversation list (question/answer pairs) and a loading flag.
- On submit: push the question into the conversation, set loading true.
- After a short `setTimeout` (stands in for network latency): check
  `TOPIC_KEYWORDS[user.department]` for a keyword match against the typed
  question. If matched, find that answer in `answers`, increment its
  `askedCount` via `setAnswers`, and push it into the conversation. If no
  match, push a synthetic `not_found` answer instead — do not call
  `setAnswers` for this case, since it isn't a real answer to store.
- Render each conversation item, showing `<TrustBadge status={...} />` next
  to any answer.

**Definition of done:** asking a seeded question (matching `TOPIC_KEYWORDS`)
returns the correct seeded answer with its real status; asking anything
else returns a `not_found` result.

---

### Step 8 — `src/verifyQueue/VerifyQueue.tsx`

**Purpose:** the only place a draft becomes canonical.

**Props:**
```
{
  user: User;
  answers: Answer[];
  setAnswers: React.Dispatch<React.SetStateAction<Answer[]>>;
}
```

**Behavior:**
- Filter `answers` to `department === user.department && status === "ai_draft"`.
- For each, render the question, the answer text in an editable text area,
  and a "Verify" button.
- Track edited text in local state keyed by answer id.
- Clicking Verify updates that answer via `setAnswers`: set
  `status: "verified"`, `text` to the edited value (or original if
  unedited), `verifiedBy: user.name`, `verifiedAt: new Date().toISOString()`,
  and clear `staleReason`.

**Definition of done:** verifying a draft removes it from this list (since
the filter only shows `ai_draft`) and, if you check `AskView` for the same
department, the same answer now shows as `verified`.

---

### Step 9 — `src/dashboard/Dashboard.tsx`

**Purpose:** read-only reporting. This component must never call
`setAnswers` or otherwise mutate anything — it only computes numbers from
`answers` and `USERS`.

**Props:** `{ user: User; answers: Answer[] }`

**Behavior:**
- If `user.functionalRole === "admin"`, compute stats across all
  departments; otherwise, only `user.department`.
- Per department: verified coverage % (`verified count / total count`),
  and count of `stale` answers (the re-verification backlog).
- A table of all answers sorted by `askedCount` descending, to surface
  recurring/unanswered questions.

**Definition of done:** verifying a draft in Step 8 changes this
component's coverage % the next time it renders, with no code change
needed in Dashboard itself — this proves the shared `answers` state is
wired correctly.

---

### Step 10 — `src/App.tsx` (wiring)

**Purpose:** the only file holding shared state.

**State:**
- `currentUser: User | null`, starts `null`
- `view: ViewName`, starts at any default
- `answers: Answer[]`, initialized from `mockData.ts`'s `ANSWERS`

**Behavior:**
- If `currentUser` is `null`, render `<RoleSelect users={USERS} onSelect={...} />`
  — `onSelect` sets `currentUser` and picks a sensible default `view` based
  on `user.functionalRole` (e.g. domain experts land on `verifyQueue`,
  managers and admins land on `dashboard`, everyone else lands on `roadmap`).
- Otherwise, render `<Sidebar />` plus whichever component matches `view`,
  passing `currentUser`, `answers`, and `setAnswers` down as needed.
- `onSwitchIdentity` resets `currentUser` back to `null`.

**Definition of done:** `npx tsc -b` and `npm run build` both succeed with
zero errors. Manually switching between all 4 personas shows the correct
default view and correct sidebar items for each.

---

## Why this design already supports fetching from the backend later

This roadmap says "hardcode everything" for now, but it's built so that
swapping to a real backend later touches **one file** — not a rewrite.
Here's why, so the agent doesn't accidentally undo this:

- **Rule 3 above is the whole reason.** Because only `App.tsx` calls
  `useState` for `currentUser` and `answers`, and every other component
  only ever receives them as props, the *components themselves* don't know
  or care whether that data came from `mockData.ts` or a real API. They
  just render whatever they're handed.

- **What actually changes when the backend is ready** — only inside
  `App.tsx`:
  ```ts
  // now:
  const [answers, setAnswers] = useState<Answer[]>(INITIAL_ANSWERS);

  // later:
  const [answers, setAnswers] = useState<Answer[]>([]);
  useEffect(() => {
    fetch(`/api/answers?department=${currentUser.department}`)
      .then(res => res.json())
      .then(setAnswers);
  }, [currentUser]);
  ```
  `RoadmapView`, `AskView`, `VerifyQueue`, and `Dashboard` need **zero
  changes** for this — they already just take `answers` as a prop.

- **The action handlers become network calls instead of local state
  updates**, but keep the same shape. Example — `VerifyQueue`'s `verify`
  function currently does `setAnswers(prev => prev.map(...))` in memory.
  Later, it becomes:
  ```ts
  await fetch(`/api/answers/${id}/verify`, { method: "POST", body: JSON.stringify({ text }) });
  const updated = await fetch(`/api/answers?department=${user.department}`).then(r => r.json());
  setAnswers(updated);
  ```
  Same `setAnswers` call at the end — just fed by a network response
  instead of a local `.map()`.

- **This is exactly why `types.ts` matters so much.** `Answer`, `User`,
  `RoadmapData` etc. aren't just for the frontend — they're the contract
  the backend needs to match. When the FastAPI routes in `backend/` are
  built, `GET /answers` should return JSON shaped exactly like `Answer[]`
  from `types.ts`. If the backend team builds against this same shape,
  swapping `mockData.ts` for `fetch()` requires no changes to `types.ts`
  or to any component at all — only to the two `useState`/`useEffect`
  blocks in `App.tsx`.

**Rule for this pass:** when writing `mockData.ts` in Step 2, shape every
mock object exactly like the real API response will eventually look — no
extra fields "for convenience" that a real backend wouldn't send, and no
missing fields that the backend will need to provide. Treat `mockData.ts`
as a stand-in for `backend/`'s future JSON, not just arbitrary test data.

## What NOT to do in this pass

- No `.css` files, no `style={{...}}` inline styles, no className styling
  logic beyond adding the class name itself for the next pass to use.
- No routing library (`react-router-dom` is not installed — don't add it;
  `view` state in `App.tsx` handles navigation for now).
- No backend calls of any kind.
- No new npm dependencies without a clear reason tied to a step above.

## Final check before handing off to the design pass

```
cd frontend
npm install
npx tsc -b
npm run build
npm run dev
```
All four must succeed. Click through all 4 personas and confirm: Roadmap's
button marks itself complete, Ask returns real answers for seeded
questions, Verify Queue's Verify button actually changes an answer's
status, and Dashboard's numbers update after a verification happens.
