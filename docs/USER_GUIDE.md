# User guide

Welcome Wave is a multi-agent onboarding system for engineering teams. One Supervisor routes the work to eight specialist
agents (Librarian, Note-taker, Guide, Planner, Scout, Reviewer, Coach and Keeper; duties in [AGENTS.md](AGENTS.md)). Agents
propose; people decide: plans, reviews and bookings wait for a person in Approvals.

This guide is organised by role. Admins and developers also want [ADMIN_GUIDE.md](ADMIN_GUIDE.md) and [API.md](API.md).

## Getting around

| What | How |
|---|---|
| Sign in | Production: Google or email (Identity Platform). Demo: the **Demo accounts** list, or an email below with the password `welcome-wave`. |
| Home | Every role lands on **Home** (`#/home`): up to six next actions, one figure, and the sections for that role. |
| Go to a page | The rail on the left, or `g` then a letter (`g h` Home, `g c` Ask, and so on; the letter is shown in the palette). |
| Search or ask | `⌘K` (`Ctrl+K`) or `/` opens the palette: pages, actions, settings, projects. Type a question and choose **Ask the project**. |
| Notifications | The bell in the top bar. Each item opens the page it is about. |
| Live updates | Pages refresh on their own when something changes. "Reconnecting…" in the top bar means updates are paused. |
| Theme | ⌘K, then *Light theme*, *Dark theme* or *Match system theme*. |
| Switch project | The project picker at the top of the rail, or ⌘K, *Switch to …*. |

Demo accounts (all `@demo.dev`, password `welcome-wave`):

| Person | Email | Role | Notes |
|---|---|---|---|
| Alex | `admin@demo.dev` | admin | |
| Priya | `priya@demo.dev` | manager | Manages every hire |
| Ravi, Meera | `ravi@`, `meera@` | expert | Ravi mentors Vijay and Tom; Meera mentors Arjun and Sara and is leaving |
| Vijay | `vijay@demo.dev` | new hire | Day 12 of 90 |
| Arjun, Sara, Tom | `arjun@`, `sara@`, `tom@` | new hire | Days 3, 45 and 88 |
| Elena | `elena@demo.dev` | executive | Read-only |

Arjun's pre-boarding page, with no sign-in: `/welcome/demo-welcome-arjun`. Managers and admins see a **Day N** control in the
top bar: the demo clock, which moves every program forward (+1 day, +1 week, or *Jump to* day 30, 60 or 90).

## Who sees which page

| Page | Route | New hire | Expert | Manager | Admin | Executive |
|---|---|---|---|---|---|---|
| Home | `#/home` | yes | yes | yes | yes | yes |
| Ask the project | `#/chat` | yes | yes | palette | palette | no |
| Code tour | `#/code-tour` | yes | palette | palette | palette | no |
| Code and KT | `#/code` | yes | palette | palette | palette | no |
| KT sessions | `#/meetings` | yes | palette | palette | palette | no |
| My tasks / Team tasks | `#/tasks` | yes (own) | palette | palette | palette | no |
| My board / Board | `#/board` (`g b`) | yes (own) | no | every hire | every hire | no |
| My program | `#/program` | yes (palette) | mentees | every hire | every hire | no |
| What your manager sees | `#/me` | yes (palette) | no | per hire | per hire | no |
| Expert inbox | `#/inbox` | no | yes | palette | palette | no |
| KT queue / KT planner | `#/kt-planner` | no | own sessions | all | all | no |
| Feedback | `#/feedback` | no | own queue | palette (all) | palette (all) | no |
| Team, Enrol, Approvals | `#/team`, `#/enrol`, `#/approvals` | no | no | yes | yes | no |
| Knowledge risk | `#/knowledge-risk` | no | no | yes | yes | no |
| Reports | `#/reports` | no | no | own project | organisation | organisation |
| Agents | `#/agents` | no | no | yes | yes | no |
| Templates, Handovers | `#/templates`, `#/handovers` | no | no | palette | yes | no |
| Setup, People, Audit log | `#/setup`, `#/people`, `#/audit` | no | no | no | yes | no |
| Settings | `#/settings` | account only (production) | documents, integrations, preferences, account | all tabs | all tabs | account |

"palette" means the page is not in that role's rail but opens from ⌘K. The server checks every route itself
([PERMISSIONS.md](PERMISSIONS.md)); hiding a page is only a convenience.

---

## New hire

**Home shows** where you are: your program day, tasks done, your current milestone and questions answered; **Your
30/60/90** (each milestone with its tasks done of all); **Now** (the current milestone's tasks, in the board's words: To do,
In progress, Waiting for sign-off, Done); **Next up** (documents to acknowledge, a failing environment check, due
check-ins, the survey, your first task, your next checkpoint, marked "overdue since" when it is late); **Open blockers**
(each said once, here and not again in Next up); your first task, KT sessions coming up, your mentor and manager, and your
questions.

### Your board

**My board** (`g b`, or **Open my board** on Home) shows your tasks by milestone (Day 15, 30, 45, 60, 90) and by column:
**To do**, **In progress**, **Done**.

- Move a card by dragging it, or focus it (`Tab`) and press `Alt` + `←` `→` to change its column, `Alt` + `↑` `↓` to change
  its place. Every card also has a `⋯` menu. `Enter` opens a card: its description, acceptance criteria, due date and history.
- **Done** means "waiting for sign-off": your manager signs it off, or asks for changes (the card goes back to In progress,
  marked "Changes requested"). A task with a pull request moves with its review.
- Only your manager moves a task to another milestone or changes the milestones; you are told when they do, and the card
  says where it came from. Milestones with a red number have overdue tasks.

### Before day one: the pre-boarding page

Your manager sends a link (`/welcome/<link>`), valid for 14 days. No account is needed.

1. Read who your manager and mentor are, and your start date.
2. Under **Before day one**, open each document and press **I've read this**.
3. Under **Your machine**, press **Copy**, paste the command into a terminal and run it. It only asks each tool (git, python3,
   docker, make for a backend track) for its version and sends the results back; it installs nothing.
4. Press **Refresh**. Each tool shows **OK** or **Failed**. Fix a failing one and run the command again.
5. **Access** shows what has been requested for you and what is granted. Your manager and mentor grant it.

An expired or replaced link says "This link no longer works": ask your manager for a new one.

### Learn the codebase

1. **Code tour** (`g o`): the code in the order it runs, chapter by chapter. Stops in teal were explained in a KT session:
   the stop says "Explained on KT #1 by Ravi, 0:20–0:55"; the play button opens that moment of the recording. **Reading
   order** shows leaves first; **Next** and **Previous** step through the stops.
2. **Code and KT** (`g k`): any file, with the KT moments that explain each part. Flag a line or a file as **Unclear** or
   **Outdated**; it goes to the file's owner.
3. **KT sessions** (`g m`): upcoming sessions (with their Meet link), and the notes of recorded ones: summary, decisions,
   action items, the code shown on screen. The transcript is for managers only.

### Ask a question

1. **Ask** (`g c`), or ⌘K and type the question.
2. The answer cites its sources: code, documents, KT moments and verified answers. Every sentence must map to a source, or
   you get "not enough evidence" instead.
3. Not it? Press **Ask a person**. The question goes to your mentor, now or in their daily digest (they get at most three
   interruptions a day unless you mark it urgent). The card says who has it and when.
4. Still stuck? Press **Ask the code owner** to send it on to whoever owns that code.
5. Rate an answer **Helpful** or **Not helpful**. A person's answer appears in the same conversation.

### Tasks and the first pull request

1. **My tasks** (`g t`) lists the tasks your manager approved. Press **Start task**.
2. Your first task's brief (in **My program**, section **First task**) says why it was chosen, which files and tests it
   touches, the KT moments that explain them, and who reviews it.
3. Before you open a pull request, press **Run private review**. Paste your diff (`git diff main...HEAD`) or give a draft
   pull request's number, then **Run review**. Only you can see the result; nothing is stored and your manager never sees it.
4. Open the pull request on GitHub from branch `task-<id>`, or write `Closes #<issue>` in it. (Demo: **Open a pull request**
   stands in for GitHub.) The task moves to *Submitted* and the Review agent writes a review; open it from **Review**.
5. Your manager decides. *Changes requested*: press **Resume work**, push again. *Approved*: the task is done and the
   milestone *First change merged* is ticked.

### My program

Open it from Home (**Open my program**) or ⌘K, *My program*. A timeline of milestones, then one section each:

| Section | What you do there |
|---|---|
| Plan | See whether your plan is approved. Tasks waiting for approval show only as a count. |
| Milestones | Read due dates; automatic ones tick themselves (documents, environment, access, first PR, check-ins, area levels). |
| Access, Pre-boarding | See what is granted and your last environment check. |
| First task | Read the brief. |
| Progress by area | Each area's level (*not started*, *exploring*, *working*, *contributed*) from kinds of evidence, and your own level next to it. No scores, no ranking. |
| Blockers | Alerts with what was seen (for example "the environment check has failed for 61 hours"). They go to you first, your mentor after one business day, your manager after three. Press **Resolve** once it is sorted. |
| Weekly summaries | Counted from your records, each line with its sources. Write this week's. |
| Check-ins | At days 30, 60 and 90: **Write your half**. Your manager writes theirs; both of you see both. |
| Onboarding survey | At days 30, 60 and 90. Anonymous: shown only as team averages of five or more. |
| Completion | The criteria for sign-off, and your manager's decision. |

### What your manager sees

**What your manager sees** (`#/me`, from My program or ⌘K) lists every data point your manager and mentor can see about you,
with the same values, and what is kept private: pre-submit reviews, survey answers, and your questions' words (the KT planner
and knowledge risk count questions, never with your name).

---

### What you are asked to accept, and why

At sign-up, or the first time you open the workspace after the texts change, you tick two boxes: the terms of service and
the privacy policy (neither is ticked for you; each opens to be read). It is recorded with the version and the date. When a
KT session is recorded you are told in the invitation, and the recording notice (`/recording`) says what is kept, who sees
it and how to ask for its removal: anyone on the project can ask from the session's page, and a manager decides. Whoever adds a
recording confirms that everyone in the session was told. Admins also accept the organisation's data-processing agreement
once. These texts are drafts awaiting a lawyer's review.

## Expert or mentor

**Home shows** "Questions waiting for you" as the figure (the response target is 24 hours), then: questions past the target
and your mentees' blockers; handover items if you are leaving; other questions and access items you own. Sections: your
digest, your mentees, the KT sessions you are asked to deliver.

### Answer questions

1. **Expert inbox** (`g i`): questions sent to you, with who asked and when. Home puts those past the 24-hour target first.
2. Write the answer as you would to a colleague, then **Send answer**. The person who asked sees it in their conversation.
3. Press **Save as verified answer**. The assistant reuses it, with its source, for the next similar question. **Verified
   answers** lists them.

### Deliver KT sessions

1. **My KT queue** (`g p`): the sessions planned for you, each with its agenda (functions, minutes at your team's pace) and
   why it is needed (a hire's task, a first PR, a gap nobody explained).
2. **Book my session**: choose **Google Meet**, **Calendly** or **Other**, a time and the invitees, then **Book**. Google sends
   the invitation; the session appears in **KT sessions** with its Meet link.
3. After the call, open the session and press **Import from Google Drive** (or upload the recording). The notes, links to the
   code and the coverage map follow.

### Look after mentees

- Home lists each mentee with their day, state and blockers. Open their program to grant access items you own
  (**Grant**, **Not needed**) and to **Resolve** blockers.
- Mentors see the program, access, areas and blockers; weekly summaries, check-ins and the completion review are between the
  hire and the manager.

### Feedback and preferences

- **Feedback** (`g f`): items people flagged as unclear or outdated in what you own. **Resolve**, **Dismiss** or **Reopen**;
  the sender is told.
- **Settings > Preferences**: **Interruptions a day** (default 3), **Office hours**, **Daily digest**.
- **Settings > Integrations**: save **Your Calendly link** so people can book you there.
- **Settings > Documents**: upload or remove the documents answers cite.

---

## Manager

**Home shows** your hires onboarding, how many are on track (on track: no overdue checkpoint and no blocker three business
days old), decisions waiting for you and open blockers; then **Next up** (blockers that reached you, plans and proposals to
approve, overdue checkpoints, due check-ins and completion reviews, access to grant, your feedback queue); **Hires and their
stage** (day of the program and the next checkpoint); upcoming check-ins; and **Recent activity** in your projects, newest
first: board moves and edits ("Priya moved 'Read the policy module' to In progress on Vijay's board"; several quick moves on
one board read as one line), enrolments, plans decided, KT sessions and recordings, check-ins, handovers. Only what happened
in your own projects appears there.

### Run the board

**Board** (`g b`) > pick a hire. Everything the hire can do, plus:

| Task | How |
|---|---|
| Sign off a finished task, or send it back | The card's `⋯` > **Sign off** or **Ask for changes**, or open it |
| Move a task to another milestone | Drag it onto a milestone tab, `Alt` + `Shift` + `←` `→`, or `⋯` > Milestone |
| Add, edit or remove a task | **+ Task**; open a card to edit or **Remove from board** (a task whose review waits in Approvals is decided there) |
| Change the milestones | **Milestones**: rename, change the day, reorder, add (one per day, up to 12), remove (its tasks move to the milestone you pick) |

You cannot manage your own board, even if your role is manager: a manager or admin of the project does it for you.

### Enrol a new hire

1. ⌘K, *Enrol a new hire* (or **Team > Enrol**).
2. Fill in **Who** (name, email, GitHub login, role title) and **Program** (template, start date, team, mentor, focus area).
3. **Enrol**. The page confirms: *Program started* (milestones, access requests, documents from the template), *Plan drafted*,
   and the invitation. No email is sent yet: tell the person to sign in.
4. Press **Review** next to *Plan drafted*.

### Approve the plan

1. **Approvals** (`g a`) lists what agents proposed: plans, pull request reviews, KT follow-up sessions.
2. A plan lists its tasks. Edit a title, description or due date; untick any you do not want.
3. **Assign N of M tasks**. The hire is told; tasks you left out are rejected.

### Choose the first task

1. Open the hire (**Team** > the hire) > **First task**.
2. The shortlist scores each candidate on five factors (focus area, explained in KT, small, tested, quiet) and lists each
   reason. Candidates touching a single-owner critical area are listed as excluded, with why.
3. **Select**. The hire gets the brief.

### Run the program

| Task | Where |
|---|---|
| Mark a checkpoint **Done**, **Skip** or **Reopen** | Program > Milestones (the program's checkpoints) |
| Request or grant access | Program > Access (**Request**, **Grant**, **Not needed**) |
| Send a pre-boarding link | Program > Pre-boarding > **Create pre-boarding link** (shown once; a new link replaces the old one). Also ⌘K, *Create pre-boarding link for …* |
| Read blockers and resolve them | Program > Blockers |
| Write your half of a check-in | Program > Check-ins > **Write your half** |
| Sign off | Program > Completion > **Sign off** or **Extend** (by N days). You may sign off with criteria unmet; the record keeps which. |
| Check what the hire sees | Program > **What Vijay sees** (`#/me/<id>`) |
| Decide a pull request review | Approvals > **Approve the work** or **Request changes** |
| Book KT sessions for gaps | **KT planner** (`g p`) > **Book session** |
| See who holds what | **Knowledge risk** (`g k`): code only one person knows, ranked by what new hires touch next; **Simulate departure** for a person |
| Plan a leaver's handover | ⌘K, *Handovers* > **New handover**; then attach documents, choose who takes over, **Move**, and **Complete handover** ([ADMIN_GUIDE.md §7](ADMIN_GUIDE.md#7-handovers)) |
| Read and export figures | **Reports** (`g r`) > **Export CSV**, or ⌘K, *Export report (CSV)* |
| Watch the agents | **Agents** (`#/agents`) |
| Invite people, connect the repository, upload documents | **Settings** (`g s`): People, Repository, Documents, Onboarding, Integrations |

---

## Admin

Admins can do everything a manager can, in every project of the organisation. **Home shows** "People on projects" (everyone
with access to at least one project; **People** lists the whole organisation), the setup steps still open, open handovers,
the hires and the organisation's recent activity.

| Task | Steps |
|---|---|
| Finish setup | **Setup** (`g u`): connect a repository, add teams, review templates, invite people, enrol the first hire, check the senior-time estimate. Each step is checked from your data; **Done**, **Skip** or **Reopen** any. Set **Minutes per avoided interruption** (default 15) and **Save**. |
| Change a role | **People** (`g e`) > the person's role list. The organisation always keeps one active admin. |
| Deactivate or reactivate | **People** > **Deactivate** / **Reactivate**. The page lists their mentees and open questions to hand to someone else. |
| Invite an admin | Settings > People > Role *Admin* (only admins can). |
| Edit a template | **Templates** (`g l`) > **Edit**. Saving makes a new version; running programs keep theirs. |
| Plan a handover | **Handovers** (`g v`) > **New handover**: who is leaving, last day. Attach documents, choose who takes over, move their work, complete it; an admin may deactivate the leaver in the same step ([ADMIN_GUIDE.md §7](ADMIN_GUIDE.md#7-handovers)). |
| Read the audit log | **Audit log** (`g d`): newest first; sort by any column, pick a time range, filter by action, person or target; 25, 30 or 50 rows a page; **Export CSV**. Viewing it is audited too ([ADMIN_GUIDE.md §8](ADMIN_GUIDE.md#8-audit-log)). |
| Connect Google Calendar | Settings > Integrations > Google Calendar (production; the demo uses a built-in calendar). |
| Export or delete the organisation | **Settings > Organisation** ([API.md](API.md#organisation-data)). |

Setup, retention, flags and limits: [ADMIN_GUIDE.md](ADMIN_GUIDE.md).

---

## Executive

Read-only: you can read, not change anything.

- **Home** shows the median days to the first merged change as the figure and the report's main figures as a section.
- **Reports** (`g r`): each figure for the current cohort (started in the last 90 days) against the 90 days before, grouped as
  *Pace*, *Follow-through* and *Knowledge*. Every figure has a **How** that says how it is worked out. Below its floor (3 hires
  for a median, 5 cases for a rate, 5 answers for a survey average) it says "not enough data", never 0.
- **Export CSV** on the page, or ⌘K, *Export report (CSV)*.
- Individual hires' programs are not shown to executives.
