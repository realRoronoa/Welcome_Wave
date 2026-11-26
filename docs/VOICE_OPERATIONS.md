# Welcome Wave Voice

Welcome Wave Voice adds a live audio assistant to a new hire's existing onboarding. Agora runs the real-time RTC conversation; Murf Falcon synthesises the assistant's speech. The assistant uses the existing Guide for code, document and KT answers and proposes writes for explicit approval in the Welcome Wave UI.

## What is connected

- `frontend/src/pages/Voice.tsx`: microphone, Agora RTC audio, Agora RTM transcript transport, approval cards, sources and call summary.
- `backend/app/voice/platform.py`: Agora Platform ephemeral session configuration, Murf Falcon BYOK secret set, six custom HTTPS tools, RTC/RTM client token, session stop.
- `backend/app/api/voice.py`: member-scoped session lifecycle and tool endpoints. Firebase remains the browser sign-in. Each cloud tool call uses a short-lived HMAC credential bound to one employee, organisation and session.
- `backend/db/migrations/022_voice.sql`: row-level protected sessions, transcripts, citations, summaries and consent-gated actions. Every session is linked to the hire's onboarding record.
- `scripts/voice-tool-proxy.py`: optional local proxy that exposes **only** the six authenticated tool paths through a temporary HTTPS tunnel. The web app remains at localhost.

## Local setup

1. In the Agora Console, obtain the project App ID and App Certificate, and the REST customer ID and secret. The project must have Conversational AI Platform access and Signaling available.
2. In Murf, obtain an API key with Falcon streaming access.
3. Put these values in the protected `backend/selfhost.env` (mode `600`). Use the variable names from `backend/selfhost.env.example`: `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, `AGORA_CUSTOMER_ID`, `AGORA_CUSTOMER_SECRET`, `MURF_API_KEY`. Never put these in `frontend/.env`, commit them, or paste them into a demo deck.
4. For cloud tool calls, provide a **public HTTPS** origin in `VOICE_TOOL_URL`. For a local demonstration, run `python3 scripts/voice-tool-proxy.py` and `ngrok http 8765`. Set `VOICE_TOOL_URL` to ngrok's HTTPS URL. Only tool paths reach the local app; the main web site stays local. A new ngrok URL requires updating the variable and restarting the API.
5. Run `./scripts/selfhost.sh release`. The command builds the API and web images, applies migration 022, and restarts the local app. The Voice page is at `http://localhost:8000/app#/voice` after signing in as a new hire with an active onboarding in the selected project.

The first live session stores the Murf key in Agora's project-scoped write-only secret set `welcome_wave_voice`. Agent sessions refer to `$secrets.welcome_wave_voice.murf`. The browser receives only the public Agora App ID and a one-hour client token; it never receives the App Certificate, REST secret or Murf key.

The credentials supplied in chat are exposed in conversation history. Rotate the Agora App Certificate and REST customer secret and the Murf API key after the demo, update `backend/selfhost.env`, and restart the API. A local ngrok URL also expires when the tunnel stops; update `VOICE_TOOL_URL` when it changes.

## Workflow and permissions

1. The hire grants microphone access and chooses **Start conversation**. The browser joins Agora RTC and RTM, then the API starts the Agora agent in that channel. The assistant's voice is Murf Falcon.
2. `getEmployeeContext` reads the signed-in hire's own profile, role, mentor, team, next steps and plan items. `checkAccessStatus` reads the hire's own access entries. Both enforce membership and onboarding relationship.
3. `searchCompanyKnowledge` calls the existing `POST /api/chat` Guide path, including its rate limit and citation check. Code file and line references and KT clips are returned from the Guide; the voice layer does not maintain a second search index. If evidence is insufficient, the answer must say so.
4. `createAccessRequest`, `requestKTSession` and `createOnboardingTask` first create **pending proposals only**. The LLM cannot approve them. The hire reviews and approves or declines each proposal in the app. Approval records the actor and timestamp, executes once, verifies a returned record ID and status, and writes to the audit log. Access stays `requested`; the KT session is unscheduled until someone books a time; a new onboarding task remains proposed until the existing task approval workflow accepts it.
5. Ending the call stops Agora and saves transcript turns, source references, completed actions, outstanding proposals and a factual call recap against the onboarding ID. The Voice page lets the hire reopen recent conversations.

## Judge demonstration

Before the judges arrive, confirm a new hire has an active onboarding in the **Welcome-Kit** project and that its repository status is Ready. The current local Welcome-Kit repository has indexed code; if no KT recording has been imported, the assistant should explicitly say there is no KT citation for that topic.

1. Sign in as the new hire. Open **Voice** from the left rail. Show the privacy note and the live audio state.
2. Start a conversation and say: “What is my role, who is my mentor, and what should I do next?” The assistant should call `getEmployeeContext` and speak the real onboarding details.
3. Ask: “How does Welcome Kit handle the onboarding experience? Show me the exact code source.” The assistant should call the existing Guide and speak a short answer with a file and line citation. Open **Sources** to show the reference.
4. Say: “Create a follow-up task to review that function, and request a KT session about it.” The assistant should offer two proposals. Approve each in the app. Show two different record IDs and their honest statuses.
5. Ask for an access status and, if relevant, propose one missing system access request. Approve it and show that it is **requested**, not granted.
6. End the call. Show the saved conversation, source references, completed action IDs and outstanding work.

If the tool tunnel is unavailable, the voice may connect but custom tools cannot reach the local API. If the Agora or Murf account rejects a live session, use the app's existing text Guide for the fallback demonstration and show the precise provider error. Do not present a prerecorded Murf clip as live audio.

## Verification and current limits

- The local release was rebuilt; `/health/ready` returns 200. The browser policy now allows the microphone, Agora's WebAssembly, and the same-origin `blob:` audio workers. The restricted HTTPS tunnel returned 401 for an invalid tool credential.
- A live Agora session started and stopped, and the authenticated `getEmployeeContext` tool returned 200 through the public HTTPS tunnel with Aman's real onboarding profile. An earlier browser RTC subscriber received the agent's playing audio track (measured peak audio level 0.845) with Murf Falcon configured. These checks confirm transport and tool reachability, not the spoken words or a complete employee conversation.
- The full backend suite passed: 1,353 passed, 100 skipped. The frontend production build passed locally. The database voice test covers the six tool paths, pending proposals, explicit approval, record creation, idempotency, audit and organisation isolation.
- This Agora Platform preview SDK rejects `advanced_features.enable_rtm` and `parameters.data_channel`, which the current Agora transcript guide requires. Live streaming transcripts and the complete spoken tool workflow have not been verified with this provider version. If the transcript panel remains empty, the call can still carry audio, and approved tool actions remain in the session record. Do not claim a saved full transcript in a judging demo until the RTM path is verified on the actual account.
- A test new hire now has an active Welcome-Kit onboarding and an approved plan. The generic task to watch a recorded KT call was not approved because no KT recording is indexed. There is no genuine KT timestamp to cite yet.

## Provider references

- [Agora Platform session lifecycle](https://docs.agora.io/en/ai/platform/get-started/start-stop-agent)
- [Agora Murf integration](https://docs.agora.io/en/ai/platform/models/tts/murf)
- [Agora custom tools](https://docs.agora.io/en/ai/platform/build/tools/custom-tools)
- [Agora browser RTC and RTM guide](https://docs.agora.io/en/ai/platform/build/build-server-client)
