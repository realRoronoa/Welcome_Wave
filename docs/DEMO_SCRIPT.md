# Demo script (5 minutes, no keys)

A walkthrough of the seeded demo organisation, in nine steps. Every step below was run against the demo API
(`MODE=demo`, 2026-11-14) as the demo users; the values quoted are what it returned. Dates are relative to the day you run it,
and a few counts (hours, seat numbers) move with the clock.

## Before you start (not timed)

1. Restart the backend so the seed is fresh (the demo is in memory and keeps whatever the last demo did):
   `cd backend && MODE=demo .venv/bin/uvicorn app.main:app --port 8080`. The web app proxies `/api` to port 8080.
2. `cd frontend && npm run dev`, open http://localhost:5173.
3. Have a terminal ready (step 4 runs the environment check).
4. Switching people: ⌘K (`Ctrl+K`), **Switch account**, then click the person under **Demo accounts**. Password, if asked:
   `welcome-wave`. Emails: `admin@`, `priya@`, `vijay@`, `arjun@`, `ravi@`, `elena@` (all `@demo.dev`).

Do not create a new pre-boarding link for Arjun before step 4: a new link replaces `/welcome/demo-welcome-arjun`.

| Step | Who | Time | Ends at |
|---|---|---|---|
| 1 Problem and positioning | visitor | 0:25 | 0:25 |
| 2 Admin: setup, knowledge risk, KT sessions | Alex | 0:40 | 1:05 |
| 3 Manager: enrol, approve, first task | Priya | 0:45 | 1:50 |
| 4 New hire: pre-boarding, learning path, cited answer | Arjun, Vijay | 0:40 | 2:30 |
| 5 First contribution | Vijay, Priya | 0:40 | 3:10 |
| 6 Knowledge reuse | Arjun, Ravi, Vijay | 0:35 | 3:45 |
| 7 Manager oversight | Vijay, Priya | 0:25 | 4:10 |
| 8 Leadership report | Priya, Elena | 0:30 | 4:40 |
| 9 Security close | Alex | 0:20 | 5:00 |

---

## 1. Problem and positioning (0:25)

*Say: a senior explains the code once; a week later nobody can find what was said.*

1. Open http://localhost:5173/. The hero reads "Learn the codebase from the people who built it."
2. Scroll. Six chapters play over the 3D office (three.js loads after the hero; without WebGL a drawn still shows the same
   words):
   - 01 *Into the screen*: "Every line remembers who explained it."
   - 02 *The KT room*: "Explain it once." Book it on Google Calendar or Calendly.
   - 03 *The floor*: "A program, not a pile of links." Thirty, sixty and ninety days, with a guided first pull request.
   - 04 *Answers*: "Answers that show their sources." Then a mentor, then the owner.
   - 05 *The lead's office*: "Leaders see ramp-up, not activity."
   - 06 *Night*: "Knowledge that stays when people leave."
3. The page ends on "Ready for your security review." (kept for step 9). Press **Log in** in the top bar.

## 2. Admin: setup, then knowledge risk and the KT sessions (0:40)

1. **Demo accounts** > **Alex (Admin)**. Home: figure **Seats 9**; next actions "Check the senior-time estimate" and
   "Handover for Meera (Senior) before …" (Meera is leaving in three weeks).
2. `g u` **Setup**: five of six steps are *done* from the data (repository, teams, templates, people, first enrolment).
   Press **Done** on "Check the senior-time estimate" (15 minutes per avoided interruption).
3. `g k` **Knowledge risk**: *Code on video* 30%, *Single-owner files* 4 / 4, *Critical files* 2, *Highest exposure* Meera.
   Under **Files by risk**, `webhooks/server.py` is critical (88): one owner, Meera, and never explained in a KT session;
   `engine/policy.py` is critical (75), owned by Ravi.
4. `g p` **KT planner**: *Recorded 30%*, *Planned +67 pts*; *Sessions* 2, *KT time* ~9.5 min, *Functions* 7; *Gap closed:
   webhooks*. The first session is "KT with Meera: webhooks/server.py", about 4 minutes, with *Why this session* (Vijay's task
   due in five days, awaiting approval; a module never explained).
5. Press **Book session**. In **Book KT session** choose **Google Meet** (the built-in demo calendar), a time, invite Vijay, and
   press **Book and send agenda**. The card now says *Scheduled* with **Open KT sessions**; the session has a
   `https://meet.google.com/…` link, and Meera is told: "Alex (Admin) booked a KT session for you to present …".
6. Optional, if there is time: book "KT with Ravi: engine/validate.py, engine/policy.py" (about 5.5 minutes) the same way.

## 3. Manager: enrol a hire, approve the plan and the first task (0:45)

1. Switch to **Priya (Manager)**. Home: figure **Hires on track: 2 of 4**; first action "Approve Vijay (New hire)'s proposed
   tasks".
2. ⌘K, **Enrol a new hire**. Name *Maya Rao*, email *maya@demo.dev*, GitHub *maya-dev*; template *Junior Backend Engineer*,
   a start date, team *Policy Engine*, mentor *Ravi (Senior)*, focus area *engine*. **Enrol**.
3. "Maya Rao is enrolled": *Program started* (milestones, access requests and documents from the template), *Plan drafted*
   ("It waits for your approval."), *Invitation sent*. Press **Review**.
4. **Approvals** shows the plan an agent drafted: "Read and summarise the webhooks module" and "First PR: Validate privileged
   init containers" (issue #12). Press **Assign 2 of 2 tasks**: "Plan approved".
5. Still in Approvals, open Vijay's waiting plan and press **Assign 2 of 2 tasks** too (his first task is used in step 5).
6. `g t` **Team** > **Maya Rao**. The timeline has 13 milestones, with check-ins on days 30, 60 and 90. Open **First task**:
   one candidate, score 1.0, with five reasons ("In the focus area (engine).", "100% of its code was explained on a KT
   call.", "Small: 2 files.", "Tests exist: tests/test_validate.py.", "Nobody else has open work on these files."). Under
   excluded: issue #15, because "engine/policy.py is a single-owner critical area: only one person could review it."
7. **Select**. The brief ends with "Run a private review of your branch before opening the pull request."

## 4. New hire: pre-boarding, the learning path and a cited answer (0:40)

1. In a new tab open http://localhost:5173/welcome/demo-welcome-arjun (no sign-in). "Welcome, Arjun." with his manager
   (Priya) and mentor (Meera).
2. **Before day one**: *Code of conduct* is read; press **I've read this** on *Security policy*.
3. **Your machine**: press **Copy**, paste into the terminal:
   `curl -fsSL http://localhost:5173/api/preboarding/demo-welcome-arjun/env-check.sh | sh`. It prints git, python3, docker
   and make with their versions, then "Sent. Reload your pre-boarding page." Press **Refresh**: each tool shows **OK**.
   **Access** shows *CI dashboards* granted and three items requested.
4. Back in the app, switch to **Vijay (New hire)**. Home: figure **Day 12**; first action "Fix your environment check:
   docker".
5. `g o` **Code tour**: 9 stops in 2 chapters, 3 explained in a KT session (about 4 minutes of recording). Open
   `validate_resource`: "Explained on KT #1: How validation works by Ravi", 0:20–0:55. Press play to watch the moment.
6. ⌘K, type "Why does validation never raise?", choose **Ask the project**. The answer cites
   `docs/CONTRIBUTING.md:L1-L6` and "KT #1: How validation works at 01:38", and says why: "Every sentence maps to a cited
   source." (Without a Gemini key the answer text opens with "Offline demo answer"; the citations are real.)

## 5. First contribution (0:40)

1. Still Vijay: `g t` **My tasks** > "First PR: Validate privileged init containers" > **Start task**.
2. **Run private review** > **Draft pull request**, number `12` > **Run review**. *Needs work*, two major findings: "No test
   files were added or changed." and "The project's guidelines say these functions return a list of violations and never
   raise …", each quoting the diff line. The panel says only Vijay can see it; nothing is stored. Close it.
3. **Open a pull request** (the demo's stand-in for GitHub). The task moves to *Submitted* with a PR chip and **Review**.
   Open **Review**: "Review agent recommends: Needs work", the same findings, and "Waiting for your decision in Approvals".
4. Switch to **Priya**. **Approvals** (`g a`) > the review > **Approve the work**: "Work approved".
5. `g t` **Team** > **Vijay** > the timeline: *First pull request opened* and *First change merged* are done; the task is
   *Done*. (The demo has no merge event: approving the work marks the task done, which ticks the milestone. See "Gaps found".)

## 6. Knowledge reuse (0:35)

1. Switch to **Arjun (New hire)**. `g c` **Ask**: "How do we roll back a policy that is blocking deployments in production?"
   Not enough evidence, so it is sent to Meera (Senior), his mentor: "They were told now (1 of 3 today)."
2. Press **Ask the code owner**: now with Ravi (Senior), "CODEOWNERS gives /engine/ to Ravi (Senior), and the answer needs
   engine/policy.py."
3. Switch to **Ravi (Senior)**. `g i` **Expert inbox**: Arjun's question. Answer it (for example "Revert the policy commit on
   main and redeploy the webhook; Priya approves the rollback in the release channel."), **Send answer**, then **Save as
   verified answer**: "Saved as a verified answer".
4. Switch to **Vijay**. Ask "How can I roll back a policy that blocks deployments?" It is answered at once, citing "Verified
   by Ravi (Senior): How do we roll back a policy …". The verified answer's reuse count goes up.

## 7. Manager oversight (0:25)

1. Still Vijay: Home shows "The environment check has failed for 61 hours" (the hours move with the time of day). ⌘K, **My
   program** > **Blockers**: stage *mentor*, "Unresolved for a business day: the mentor was told." It went to Vijay first,
   then to Ravi.
2. Switch to **Priya**. Press the **Day 12** control in the top bar > **+1 week**. The bell shows "Vijay (New hire) may be
   blocked: The environment check has failed for …"; Home's figure drops to **1 of 4** on track and the first action is
   "Unblock Vijay (New hire): …".
3. **Team** > **Vijay** > **Blockers**: stage *manager*, "Unresolved for three business days: the manager was told."
4. Press **What Vijay sees**: the same 18 data points, with the same values, that Vijay sees on his own page, and what is kept
   private (pre-submit reviews, survey answers, his questions' words).

## 8. Leadership report (0:30)

1. Still Priya: the **Day 19** control > **Jump to Day 90**.
2. **Team** > **Vijay** > **Completion**: day 90 of 90, five criteria with their evidence (milestones still open, the day-90
   check-in by both, no open blockers, a merged change in an area, access settled). **Sign off** is allowed with criteria
   unmet; the record keeps which.
3. `g k` **Knowledge risk**: key-person risk is still Meera (Senior), 0 % of her code explained, with about 4 minutes of
   handover; 2 critical files.
4. Switch to **Elena (Executive)**. `g r` **Reports**, current cohort against the previous one. At day 90 the previous cohort
   reads: median days to the first pull request *12 days*, to the first merged change *16 days*, milestones on time *62 %*,
   check-ins on time *38 %*. *Senior time saved (estimate)*: *0.8 hours* (current) against *0.2 hours*, with the assumption
   (15 minutes per avoided interruption) under **How**. Figures below their floor say "not enough data", never 0.
   **Export CSV** downloads the same figures.

## 9. Security close (0:20)

1. Switch to **Alex (Admin)**. `g d` **Audit log**: "Kept 365 days · viewing it is audited too". This demo's actions are
   there: `kt_plan.scheduled`, `onboarding.enrolled`, `approval.task_plan.decided`, `first_task.selected`,
   `preboarding.acknowledged`, `preboarding.env_checked`, `approval.review.decided`, `question.routed`, `expert.answered`,
   `answer.verified`, `demo.clock_advanced`. **Export CSV**.
2. Data controls: an admin downloads the whole organisation (`GET /api/org/export`, about 160 KB of JSON in the demo) or
   deletes it after typing its name (`DELETE /api/org`). Both are audited. There is no page for them yet: show the call.
3. Read-only code access: the GitHub App asks only for read permissions, and the code's repository interface has no write
   methods. Close on the landing page's "Ready for your security review." grid.

---

## Gaps found

What could not be done exactly as the storyline says, and the path used instead.

| Step | Gap | Path used |
|---|---|---|
| 2 | Booking in the demo always succeeds, and **Import from Google Drive** always "finds" a recording; with the offline model the notes say "No speech was detected.", so booking does not change coverage or knowledge risk. | Show the booking and the Meet link only. |
| 3 | The plan an agent drafts covers the first 30 days (`PLAN_HORIZON_DAYS`, two tasks in the demo); the 30/60/90 shape is in the milestones, not in the plan. The template's seven plan items are used only when no model is available. | Show the plan, then the milestones on the timeline. |
| 3 | The enrolment page says "Invitation sent", but no email is sent (the Mailer port is planned). In the demo the invitee is listed under **Invited** on the sign-in page. | Say so. |
| 4 | Creating a pre-boarding link for Arjun (Program > Pre-boarding, or the ⌘K action) replaces `/welcome/demo-welcome-arjun`, which then says "This link no longer works" until the backend restarts. | Do not create one before step 4. |
| 5 | There is no demo merge. Approving the review marks the task done, which ticks *First change merged*; in production the same happens on approval, before GitHub's merge event. | Approve the work, then show the timeline. |
| 6 | "Routes to the owner with a draft": the AI draft is stored with the escalation (in the metrics) but is not shown to the owner anywhere (inbox, Home or API). With the offline model the draft is empty anyway. | Show the routing reasons instead. |
| 6 | Vijay's mentor and his code's owner are both Ravi, so Vijay cannot show the owner tier ("Ravi (Senior), the domain owner, already has this question."). | Arjun asks (mentor Meera, owner Ravi). |
| 6 | After Ravi answers, the copy of the question with Meera (the mentor) stays open in her inbox. The reused answer cites the same verified answer twice (one source from answering, one from saving it). | None needed on screen. |
| 8 | At day 90 the completion review shows "A merged change in at least one area: No merged change yet" although Vijay's first change merged in step 5: the first-PR task made from an issue has no file paths, so the change counts towards no area. | Point at the other criteria, or open Tom's program (contribution met in *engine*). |
| 8 | Jumping to day 90 moves every program, so the current cohort's figures fall (milestones on time 0 %, most medians "not enough data"). At day 12 the same report reads 4 hires, 12 days to the first PR, 16 days to the first merged change, 70 % milestones on time, 100 % check-ins on time, 1.0 hours saved. | Read the previous cohort's column at day 90, or open Reports once before the jump. |
| 8 | Key-person risk is not in the leadership report, and executives cannot open Knowledge risk (403). | Priya shows Knowledge risk before switching to Elena. |
| 9 | Organisation export and deletion have no page; they are API calls. | Show the call, or the export's size. |
