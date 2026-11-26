# Welcome Wave hackathon demo: Welcome-Kit as the evidence source

The short narrated animated overview is [welcome-wave-welcome-kit-demo.mp4](video/welcome-wave-welcome-kit-demo.mp4). Use the steps below for the real screen recording, which shows actual app responses.

Welcome Wave is the product in this recording. [Welcome-Kit](https://github.com/VijayabaskarR-06/welcome-kit) is the small public engineering repository and onboarding experience that Welcome Wave indexes and explains. Its mission ticks and field notes are browser-local; they do not sync into Welcome Wave.

## Before recording

1. Keep the Welcome Wave API, web app, PostgreSQL, and HTTPS voice-tool tunnel running. Check `http://localhost:8000/health/ready` and **Settings → Integrations**. Do not show the environment file, provider consoles, tokens, or ngrok inspector on screen.
2. In the **Welcome-Kit** project, confirm **Settings → Repository** shows `VijayabaskarR-06/welcome-kit` as **Ready**. The code index is real. There is currently no indexed KT recording, so do not promise a KT timestamp citation.
3. With the manager account, go to **Team → Enrol** and enrol the demo hire in the Welcome-Kit project as a backend engineer. Choose a mentor, template, start date, and focus area. In **Approvals**, review and assign the drafted plan. The Voice page needs this active onboarding.
4. Sign in as that hire in a second browser profile. Rehearse **Ask the project** with “Where is mission progress stored, and what happens when I complete a mission?” Check that the answer shows an actual citation to `src/lib/mission.ts` or `src/App.tsx`.
5. Rehearse a short Voice call with headphones and microphone permission. Agora's current Platform preview has not produced a verified streaming transcript in this app. Keep the text Guide recording as the reliable evidence path. Film only voice actions, citations, and saved turns that actually appear.

## Four-minute recording

| Time | Show | Narration |
| --- | --- | --- |
| 0:00–0:20 | Welcome Wave home, then a very short Welcome-Kit orbit and mission clip. | “A new engineer meets a beautiful welcome experience, but their real questions and work need company context. Welcome Wave connects the two.” |
| 0:20–0:55 | Manager: Welcome-Kit project, repository **Ready**, then **Team → Enrol** and the real drafted plan. | “We index the actual application repository and create an onboarding program for this engineer. This is a real project and a real hire record.” |
| 0:55–1:45 | Hire: **Ask the project**. Ask the mission-progress question. Pause on the answer and cited file. | “The engineer asks in plain language. Welcome Wave searches the repository and grounds the explanation in code they can inspect.” |
| 1:45–2:20 | **Code Tour**: open the mission click path from `src/App.tsx` into `src/lib/mission.ts`. | “The citation is a route into the implementation, not just a decorative footnote.” |
| 2:20–3:05 | Show the hire board and manager approval flow. If Voice proposal tools work in rehearsal, show a live proposed task and KT request, approve them, and show the real record IDs and statuses. | “An agent can prepare work, but the person approves the write. An access request does not grant access; a KT request is not a booked meeting.” |
| 3:05–3:40 | Optional rehearsed **Voice** clip: start call, ask who the mentor is, ask the same code question, show actual audio and any real tool result. | “Agora carries the conversation and Murf Falcon is configured for the assistant speech. We show the live evidence available from this session.” |
| 3:40–4:00 | Return to Welcome Wave source citation and onboarding plan. | “Welcome-Kit makes day one inviting. Welcome Wave turns project knowledge into an accountable path to a first contribution.” |

Record at 1080p with system audio and microphone. Cut loading time, but keep each action and its result together. Zoom into the repository status, code citation, and one real action ID. If a live Voice transcript or tool result is absent, leave that shot out and keep the verified Guide and onboarding sequence. Never substitute prerecorded speech for a claimed live call.

## What the judge can verify

- Public Welcome-Kit source and website; real code indexed in the Welcome-Kit project.
- An onboarding record and approved plan made in Welcome Wave.
- A Guide answer with a specific code citation and the corresponding Code Tour.
- An action record with its actual status when the live Voice approval sequence has been rehearsed successfully.

The current hackathon hosting plan keeps the persistent API, database, and workers on the demo laptop. A temporary HTTPS link is live only while that laptop and its tunnel are running. A Vercel frontend, if used, must proxy to that live backend; it is not a standalone full-product deployment.
