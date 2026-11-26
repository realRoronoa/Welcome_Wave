# Present Welcome Wave with ORBIT / Welcome Kit

**Checked on 18 November 2026.** Use this for a 12–15 minute product demonstration. The first half shows the real, persistent local installation and its public Welcome Kit repository. The second half uses Welcome Wave's separate seeded demo to show roles and lifecycle events that need multiple people. Label the switch clearly.

## Set up before the audience arrives

1. Open a clean browser window with three tabs. Use a fresh browser profile for the Welcome Kit if you want its progress ring to begin at 0%:
   - **Welcome Kit:** https://vijayabaskarr-06.github.io/welcome-kit/
   - **Real Welcome Wave:** http://localhost:8000/login — Continue with Google using the existing admin account, then select the **welcome kit** project.
   - **Seeded Welcome Wave demo:** http://localhost:5173/login — choose **Priya (Manager)**. The seeded accounts use the password `welcome-wave` if prompted.
2. From the Welcome Wave repository, run `./scripts/selfhost.sh status`. The API and web app should be ready. In the real app, open **Settings → Repository** and confirm `VijayabaskarR-06/welcome-kit` says **Ready**. It indexed 17 files and 36 chunks at commit `40684b6` on 18 November. If the repo has changed, the counts may differ.
3. Check the live Welcome Kit and its [GitHub repository](https://github.com/VijayabaskarR-06/welcome-kit). Keep the three `good first issue` issues and the green Actions checks ready to show.
4. Keep [the 4:44 explainer](video/welcome-wave-workflow-explainer.mp4) available as a backup. It is an animated example, not evidence that real users completed the shown workflow.
5. The real localhost installation currently has one admin, no enrolled hire, no uploaded project document, and no recorded KT session. Use the seeded demo for those parts. Do not claim its records are from the real installation.

Do not display `backend/selfhost.env`, API keys, personal inboxes, or browser tabs with private information during the presentation.

## The 12–15 minute live script

| Time | Screen and action | What to say |
| --- | --- | --- |
| 0:00–0:45 | Start on Welcome Wave's public page at `http://localhost:8000/`, then open Welcome Kit. | “A new engineer needs more than links. They need a path into the code, the people who know it, and a safe first contribution. Welcome Wave turns those pieces into one onboarding workspace.” |
| 0:45–2:45 | On the public Welcome Kit, drag the 3D orbit; press **Start your journey**; switch between the three phases; mark one mission; open one Toolkit resource; type a harmless example in Field Notes. | “This is the actual sample application the new engineer will learn. Its first-day, first-week and first-month missions are interactive. The notes and ticks stay in this browser; they are a personal guide, not Welcome Wave program records.” |
| 2:45–4:45 | Switch to real Welcome Wave at `localhost:8000`. Open **Settings → Repository** and show **Ready**. Press `⌘K` or `Ctrl+K`, choose **Code and KT**, and open `src/lib/mission.ts` or `src/scene/OrbitalCanvas.tsx`. Then choose **Ask the project** and ask “Where does a completed mission get saved?” Open a citation if an answer appears. | “This is a real, persistent installation reading the public GitHub repository. The code and Markdown guides are indexed. An answer should point back to source; we inspect the citation. If the evidence is weak, the app should say so.” |
| 4:45–5:45 | Open GitHub: repository README, `docs/ARCHITECTURE.md`, three `good first issue` issues, and green Actions checks. | “The sample is independently deployed, tested, and documented. GitHub issues give a newcomer a real first contribution. Welcome Wave reads this repository; it does not write to it.” |
| 5:45–6:00 | Switch to the **seeded demo** at `localhost:5173` and say the switch aloud. | “Now I’m switching to a seeded, in-memory example so you can see what the complete multi-person workflow looks like. These accounts and activity are examples.” |
| 6:00–7:30 | As **Priya (Manager)**, show Home and Team. Use `⌘K` → **Enrol a new hire**, enter a made-up person such as Maya Rao (`maya@demo.dev`), select a template, then **Enrol**. Open **Approvals**, inspect the draft plan, and assign the selected tasks. | “The manager chooses the program and remains responsible for approving the plan. Agents propose tasks; people decide what is assigned.” |
| 7:30–9:15 | Switch demo account to **Vijay (New hire)**. Open **Code tour** (`g o`), then **Code and KT** (`g k`). Open an explained stop and its KT clip. In **Ask**, use “Why does validation never raise?” and inspect the cited KT and code/document evidence. | “The hire can follow code in execution order, jump to the exact recorded explanation, and ask a question with cited sources. The seeded demo may label its response ‘Quoted from the sources’ when an offline model is used.” |
| 9:15–10:15 | Still Vijay, open **My tasks**, select a task, then show **Run private review** if a first task is available. Do not submit a real GitHub PR in this scripted demo. | “A newcomer can check a draft before asking for human review. Their private pre-submit review stays private; the manager only sees a formal review after a PR.” |
| 10:15–11:30 | Switch back to **Priya**. Show **Knowledge risk**, **KT planner**, and **Reports**. Point at one unrecorded or single-owner area and the suggested KT session. | “Managers see where knowledge is concentrated and can book a focused session. Reports show onboarding evidence and explain assumptions, rather than inventing success numbers.” |
| 11:30–12:30 | Close on Welcome Kit's live page or the real repository. | “The public sample, GitHub workflow, and real local repository index are live today. The seeded demo illustrates the steps that require a mentor, a hire, a recording, and a reviewed PR. The next real step is inviting those people and running the first session.” |

For a 5-minute version, show the kit (1 minute), the real repository and cited answer (2 minutes), then in the seeded demo show Priya's approval and Vijay's code tour (2 minutes). Skip live enrolment and reporting.

## Click paths and prepared questions

- In the **real** installation, a manager/admin's left rail has Home, Team, Approvals, People, Handovers, Templates, KT planner, Knowledge risk, Reports, Agents, Setup, Audit log and Settings. **Ask**, **Code tour**, **Code and KT**, and **KT sessions** are available through `⌘K`/`Ctrl+K` for managers even when they are not in the rail.
- In the **seeded demo**, `g h` opens Home, `g t` Team (manager) or My tasks (hire), `g a` Approvals, `g o` Code tour (hire), `g k` Code and KT (hire) or Knowledge risk (manager), and `g p` KT planner. The letter appears next to each navigation item.
- Real Welcome Kit questions: “Where is mission progress persisted?”, “How does selecting a phase change the orbit color?”, “What happens if WebGL is unavailable?” Open the answer's source link. The first answer should lead to `src/lib/mission.ts` and `src/App.tsx`; the second and third to `src/scene/OrbitalCanvas.tsx`.
- Seeded demo question: “Why does validation never raise?” The sample data includes a KT explanation and contribution guide for that behavior. The wording or citations can vary after demo actions.
- If Ask says **not enough evidence**, say “That is the evidence gate working,” then open the relevant code file directly. Do not paraphrase an unsupported answer as fact.

## What is real, and what is still an example

| Claim | Current evidence / limit |
| --- | --- |
| Welcome Kit site and source | Public GitHub Pages site and repository; CI and deployment passed. |
| Real repository search | `welcome kit` project in the persistent localhost installation is **Ready** at commit `40684b6`; 17 files and 36 chunks indexed. Check citations during the demo. |
| Real people and onboarding | Only the admin is in that project. A real hire, mentor, invitation, plan, and PR have not yet been created. |
| KT-to-code | The seeded demo has sample KT coverage. The real project has no recorded KT session, so it should not show code as explained on video yet. |
| Google Calendar/Drive | The local organisation connected Google; API read probes passed. A real session booking or recording import has not been verified. |
| Email | Gmail SMTP login passed, without a test message. Do not promise that a specific invitation was delivered. |
| Calendly | The localhost installation cannot receive public webhooks. |
| Audience access | The Welcome Kit site is public. `localhost:8000` and `localhost:5173` are only on the presenter's machine. |

## If something fails during the presentation

- **Local app unreachable:** run `./scripts/selfhost.sh status`; if needed, `./scripts/selfhost.sh release` rebuilds and starts the persistent release. Keep the 4:44 video available while it starts.
- **Repository is Reading or Failed:** use GitHub's source and the live kit for the walkthrough. State the current index status; do not claim Ask is using the latest commit. Re-index from **Settings → Repository** after checking the Gemini quota.
- **Ask has no cited answer:** inspect `src/lib/mission.ts` in **Code and KT** or GitHub and explain the source. Do not use a prepared fake answer.
- **Seeded demo has been changed by rehearsals:** restart its separate `MODE=demo` backend to restore seeded data. The seeded data is in memory and is independent of the real installation.
- **No recording:** use the KT plan in the Welcome Kit repository and show the seeded example. A real recording must be uploaded and processed before claiming coverage.

## Questions you may get

**“Is this a real product or just a demo?”** The `localhost:8000` installation uses persistent PostgreSQL, Firebase sign-in, a real public GitHub repository, and the live Gemini-backed index. The `localhost:5173` seeded mode is an illustrative scenario with made-up people and activity. The local release is not yet a public team deployment.

**“Does checking a mission in the Welcome Kit update Welcome Wave?”** No. The kit saves personal progress in that browser. Welcome Wave tracks a hire's assigned program, tasks, KT evidence, and approvals separately.

**“Does the AI decide what people do?”** It proposes answers, plans, and reviews with source evidence and guardrails. Managers approve plans and reviews; the app does not merge or write code to GitHub.

**“What do we need to go live with a real team?”** A reachable HTTPS host, real people invited to the organisation, a reviewed onboarding template, a real KT recording, and a billing/usage plan for Gemini. Those are operational steps beyond this local demonstration.
