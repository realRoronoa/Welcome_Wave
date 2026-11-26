# Legal: what exists, what to fill in, what to decide

Welcome Wave now carries terms of service, a privacy policy, a data-processing agreement, a sub-processor list, a
recording and AI notice and acceptable-use rules, and records who accepted what.

**These texts are first drafts, written by an AI, not by a lawyer, and they are not legal advice.** They are careful and
they match what the code does, but no lawyer has read them. Have one read them before a paying customer accepts them.
Everything a lawyer or a founder must decide is listed on this page, so nothing was chosen for you without saying so.
`LEGAL_REVIEW.status` in `frontend/src/legal/documents.ts` is `"draft"` until that review is done; the generated copies
say so at the top.

## What exists

| Document | For | Accepted by | Version key | Source | Text to read |
|---|---|---|---|---|---|
| Terms of Service | Everyone | Each person; at sign-up, on joining by invitation, and when asked in the product | `terms` | `frontend/src/legal/terms.ts` | [legal/TERMS.md](legal/TERMS.md) |
| Acceptable Use | Everyone | With the terms: it is part of them | `terms` | `frontend/src/legal/acceptable_use.ts` | [legal/ACCEPTABLE_USE.md](legal/ACCEPTABLE_USE.md) |
| Privacy Policy | Everyone, and anyone in a recording | Each person, with the terms | `privacy` | `frontend/src/legal/privacy.ts` | [legal/PRIVACY.md](legal/PRIVACY.md) |
| Recording and AI Notice | Everyone in a KT session; anyone adding a recording | Each member acknowledges it once per version | `recording` | `frontend/src/legal/recording.ts` | [legal/RECORDING.md](legal/RECORDING.md) |
| Data Processing Agreement | Customers' admins | An admin, once for the organisation | `dpa` | `frontend/src/legal/dpa.ts` | [legal/DPA.md](legal/DPA.md) |
| Sub-processors | Customers' admins | Nobody: published and dated | `subprocessors` | `frontend/src/legal/subprocessors.ts` | [legal/SUBPROCESSORS.md](legal/SUBPROCESSORS.md) |

Behind them: [legal/DATA_INVENTORY.md](legal/DATA_INVENTORY.md) lists every kind of data, where it goes and the file that
proves it. The documents claim nothing the inventory cannot back.

### Where it shows in the product

| Where | What happens | Code |
|---|---|---|
| The documents' pages: `/terms`, `/privacy`, `/recording`, `/acceptable-use`, `/dpa`, `/subprocessors` | One reading page renders `frontend/src/legal/documents.ts`, filling in the operator's details from `GET /api/config` (`legal`): "In brief" first, the version and the date it took effect, a contents list, an anchor per section, a print stylesheet. They open without the intro. While `LEGAL_REVIEW.status` is `"draft"`, each page says so | `frontend/src/site/Legal.tsx`, `frontend/src/legal/paths.ts`, `backend/app/legal.py` (`public`) |
| The site's footer, and under every sign-in screen | Links to the documents | `frontend/src/site/Footer.tsx`, `frontend/src/auth/Entry.tsx` |
| Creating a login, creating an organisation, joining by invitation | An unticked checkbox, "I agree to the Terms and have read the Privacy Policy", with both documents linked. The request must carry it, or nothing is created | `frontend/src/auth/Agree.tsx`, `backend/app/legal.py` (`require_on_joining`) |
| Everyone who joined earlier, and every new version | After sign-in, a consent screen lists what is new or updated, links each document and has one action. There is no way past it except signing out; leaving, and an admin's export and deletion, stay reachable on it. `GET /api/legal/status`, `POST /api/legal/accept` | `frontend/src/consent/LegalGate.tsx`, `frontend/src/consent/ConsentScreen.tsx`, `backend/app/api/legal.py` |
| The recording notice | Shown at the top of KT sessions until the member acknowledges it, once per version; asked for again in the step that adds a recording | `frontend/src/consent/RecordingNotice.tsx` |
| The data-processing agreement | Settings, Organisation, Legal: an admin accepts it for the organisation with one explicit confirmation (`POST /api/org/legal/dpa`), sees who accepted and when (`GET /api/org/legal`), and downloads a record of it (`GET /api/org/legal/dpa/record`). The same section lists every document and the operator's details in use, and says which are not set | `frontend/src/pages/settings/Legal.tsx`, `backend/app/api/legal.py` |
| Settings, Account | Every document, and when the person accepted each of theirs | `frontend/src/pages/settings/Legal.tsx` (`YourDocuments`) |
| Adding a recording | Upload and Drive import are refused until a member ticks "Everyone in this session was told it is recorded and agreed to it." The session keeps who, when and the notice's version, and shows it. The schedule form says who is told and that a recording waits for this confirmation | `frontend/src/pages/Meetings.tsx`, `backend/app/services/recordings.py`, `backend/app/api/recordings.py` |
| Calendar invitations | The invitation for a KT session booked through Welcome Wave says it will be recorded and links to the recording notice | `backend/app/services/recordings.py` (`invitation_notice`) |
| Removing a recording | Anyone on the project can ask; a manager removes it or declines with a reason, and the person is told | `backend/app/api/recordings.py` |
| The audit log | `legal.accepted`, `legal.dpa_accepted`, `recording.consent_confirmed` | `backend/app/legal.py`, `backend/app/services/recordings.py` |

An acceptance is one append-only row: who, which document, which version, when, how, the network it came from (never
the full address) and the browser's description (`backend/db/migrations/019_legal.sql`).

`LEGAL_ENFORCE` is off by default: the web app asks, and the API refuses nothing. Turn it on only when the deployed web
app is the one that shows the prompt, or people are locked out with nothing to click (`backend/app/config.py`).

## Settings to fill in

The documents never name your company in their text. They read these settings, and show a neutral phrase while one is
empty. None is needed to start the server. `./scripts/selfhost.sh check` lists the ones still missing.

| Setting | What to put | While empty, the documents say |
|---|---|---|
| `LEGAL_OPERATOR_NAME` | The registered legal name of the company that runs the service | "the company that operates this service" |
| `LEGAL_OPERATOR_ADDRESS` | Its registered address, on one line | "the operator's registered office" |
| `LEGAL_CONTACT_EMAIL` | Where legal notices and questions go | "the support contact for this service" |
| `LEGAL_PRIVACY_EMAIL` | Where privacy requests and grievances go. Falls back to the contact address | "the privacy contact for this service" |
| `LEGAL_GOVERNING_LAW` | A whole phrase that completes "governed by …", for example "the laws of India" | "the laws of the place where the operator is established" |
| `LEGAL_COURTS` | A whole phrase that completes "Disputes go to …", for example "the courts of Chennai, India" | "the courts of that place" |

Fill all six before a customer accepts anything: a contract that names nobody is hard to rely on. Use mailboxes that
someone reads; the documents promise answers from them.

## Decide with your lawyer

Each line is a choice or an assumption the drafts make. The defaults are conventional, and none is right for every
company. Section numbers are the documents' own.

### All documents

- **Who the contract is with.** The drafts assume one registered company runs the service and signs with customers. If
  the company is not registered yet, decide who is the operator until it is.
- **Click-through acceptance.** A member's click accepts the terms for the organisation they create. Decide whether a
  paying customer also signs an order (the terms let a signed order win where the two differ).
- **A customer running Welcome Wave on its own servers.** The drafts assume you host it. A customer that installs it
  itself is its own operator: these terms are not a software licence, and it needs one.
- **Languages and countries.** English only. The drafts cover GDPR and UK GDPR, India's DPDP Act and California's CCPA
  at the level of a first draft. No other country's law was considered.

### Terms of Service

- **§1 Order of documents.** A signed order beats the terms.
- **§2 Availability.** No uptime commitment and no support hours. Customers may ask for both.
- **§2 Changes to the service.** You may change it; a customer that loses something it relies on may leave.
- **§3 Age.** 18 and over.
- **§5 Content.** The customer keeps ownership; you get a licence only to run, secure and support the service. You may
  use content-free operational figures and any product feedback.
- **§6 AI output.** No promise that it is correct. Customers must not make it the only basis for a decision that
  seriously affects a person.
- **§7 Recordings.** Recording law is the customer's responsibility.
- **§10 Fees.** No price is written. Defaults: invoices due in 30 days; taxes such as GST added on top; no refund when
  the customer leaves early; no late interest; no renewal or price-increase terms; no currency.
- **§12 Ending.** The customer may leave at any time. You may end with 30 days' notice and a refund for the unused
  time. 30 days to put a serious breach right. Suspension for a security threat, unlawful use, or an invoice 30 days
  late after a reminder. Deletion within 30 days after the end.
- **§13 Warranty.** "Reasonable skill and care", otherwise as it is.
- **§14 Liability.** Each side's total is capped at the fees for the previous 12 months. **In a free pilot that cap is
  zero, which a court may not accept: set a minimum amount.** Neither side pays for lost profits or indirect loss. The
  only carve-outs are what the law forbids limiting, and fees owed. Customers often ask for a higher cap for data
  protection and confidentiality.
- **§15 Claims by others.** The customer defends you over its content and unlawful recordings; you defend it over
  intellectual-property claims against the service. Neither is carved out of the cap. What you may do if the service
  is found to infringe (replace, change, refund) is not written.
- **§16 Changes to the terms.** 30 days' notice of a change that reduces the customer's rights.
- **§17 Law and courts.** From the settings. Decide between courts and arbitration; Indian business contracts often
  choose arbitration. Notices by email. Transfer on a merger or sale without consent.
- **Left out on purpose:** service levels, the right to use a customer's name or logo, insurance, anti-bribery and
  export-control wording beyond one line, and stamp duty or e-signature formalities.

### Acceptable Use

- The list of what must not be added (health, payment card and identity numbers) and the right to remove content or
  suspend an account.

### Privacy Policy

- **§1 Roles.** The customer is the controller of workspace data and you are its processor; you are the controller of
  sign-in accounts, acceptance records, security logs and customer contacts. Confirm that split.
- **§3 Legal grounds.** Contract and legitimate interests under GDPR. Under the DPDP Act the draft relies on consent
  given by accepting the policy, or a use the Act allows without consent. Whether an employee's consent is free enough,
  and whether a separate notice in the Act's form is needed, are questions for your lawyer.
- **§4 Staff access.** "Only when needed to run, secure or support the service." You must be able to keep that.
- **§5 AI.** How Google's terms are described, and the warning about free Gemini API keys.
- **§7 Transfers.** That data may be processed outside the reader's country, including in the United States, under
  standard contractual clauses "or another mechanism".
- **§8 How long.** Workspace data never expires on its own. Regulators expect a reason for every period.
- **§9 Rights.** Answers "within the time the law sets". There is no download for one person: an admin exports.
- **§10 Browser storage.** One cookie and functional storage, with no consent banner. Confirm that none is needed.
- **§12 Children.** Under 18.
- **§14 Contacts.** One mailbox serves as privacy contact and grievance address. No data protection officer and no
  EU or UK representative is named.

### Data Processing Agreement

- **Acceptance.** An admin's click, with a downloadable record. Some customers will want a signed copy, or their own
  agreement instead.
- **§7 Sub-processors.** General permission, 30 days' notice, a right to object, and a refund if the customer leaves
  over it.
- **§8 Transfers.** The EU standard contractual clauses by reference: Module Two or Three, the docking clause, the law
  and courts of Ireland. The UK Addendum and Swiss law in one sentence each. The other options in the clauses, the
  supervisory authority and a transfer impact assessment for India and the United States are not written.
- **§10 Breaches.** Notice within 72 hours of becoming aware. GDPR asks a processor only for "without undue delay".
- **§11 Audits.** Once a year, 30 days' notice, at the customer's cost.
- **§12 Deletion.** Within 30 days after the end. No certificate of deletion is promised.
- **§13 California.** Service-provider wording. Check it against the current regulations.
- **§14 India.** One paragraph. Check it against the Act and its Rules as they come into force.
- **§15 Liability.** The terms' cap applies to the agreement.
- **Annex 1.** "No sensitive data intended." Voices and faces are in every recording; the draft says they are not used
  to identify anyone. Confirm how each law you sell under classes them.
- **Annex 2.** A statement of fact on its date. It must change when `docs/SECURITY.md` does.

### Sub-processors

- **Legal names.** The list says "Google Cloud", "Google Identity Platform", "Google Vertex AI", "Google Gemini API" and
  "LangSmith (LangChain, Inc.)". Put in the exact contracting entities and addresses from your own accounts.
- **Your email provider.** Named only as "an email provider of the operator's choice". Name it.
- **Your server.** If the service runs on a machine you rent, its hosting company belongs on the list by name.
- **Connected services.** GitHub, Google Calendar and Calendly are treated as the customer's own services, not your
  sub-processors. The Calendly webhook is set up on your server, so confirm that view.

### Recording and AI Notice

- **What the confirmation is.** A member states that everyone was told and agreed. Welcome Wave cannot check it.
  Decide whether that is enough where you sell, or whether each participant's own agreement must be captured.
- **Agreement at work.** In Europe an employee's consent to their employer is often not treated as freely given.
  Customers there may rely on another ground and a right to object; the notice says "agreement" throughout.
- **§3 "No voiceprints and no face templates."** True of the code. Laws on biometric data differ by place.
- **AI at work.** Some laws treat AI used to follow employees' progress as high-risk, and some countries give works
  councils a say before such a tool is introduced. The product tracks onboarding progress and drafts recommendations
  that a manager decides. Ask how those rules apply to your customers.
- **§5 How long.** 30 days for the video file on Google Cloud. No limit on a single server unless you set one.

## Promises kept by hand

The code does not keep these for you. Someone has to.

| Promise | Where it is made | What it takes |
|---|---|---|
| Tell admins of a breach within 72 hours | DPA §10 | A written incident procedure, which `docs/SECURITY.md` §12 lists as planned, and a list of each customer's admins |
| Delete a customer's remaining data within 30 days after the agreement ends | Terms §12, DPA §12 | Someone runs the organisation's deletion if its admin did not |
| Tell admins 30 days before adding a sub-processor | DPA §7, Sub-processors §5 | Publish a new version of the list, and write to admins: nobody is prompted to accept that list |
| Tell admins 30 days before a change that reduces their rights | Terms §16 | The same |
| Remove a person's sign-in account at Identity Platform on request | Privacy §9 | Done in the Firebase or Google Cloud console |
| Pass a person's request to their organisation, and answer in the time the law sets | Privacy §9, DPA §9 | Someone reads the privacy mailbox |
| Reach workspace data only when needed; staff bound to confidentiality | Privacy §4, DPA §5 | Confidentiality terms with everyone who has server access |
| Answer a security questionnaire once a year; allow an audit | DPA §11 | Time |
| Bind each sub-processor by contract | DPA §7 | Accept Google's data-processing terms in each account; the same for an email provider and LangSmith if used |
| Tell admins before a merger or sale moves their data | Privacy §6 | |
| Refunds on ending without cause, or after an unresolved objection | Terms §12, DPA §7 | Invoicing is outside the product |

## Check before selling

These need a person, an account or a lawyer. The points of law are from general knowledge and were not checked against
current law.

1. **Recording-consent law, by region.** The product asks for everyone's agreement everywhere, which is the safe rule.
   Confirm it for each place you sell, and for guests who join from elsewhere:
   - **United States.** Federal law and most states need one participant's consent. Several states, California among
     them, need everyone's. Get the current list.
   - **European Union and United Kingdom.** GDPR needs a lawful ground and clear notice. Some countries also make
     recording a conversation without consent a crime.
   - **India.** No single call-recording statute; the constitutional right to privacy, the IT Act and the DPDP Act apply.
2. **India's DPDP Act.** Its Rules bring duties into force in stages. Confirm what applies on the day you sell: the
   form and languages of the notice, how consent is withdrawn, whether a consent manager matters to you, the contact
   who answers data questions, breach reports to the Data Protection Board, and whether you could be named a
   significant data fiduciary. Until the Act fully applies, the IT Act's 2011 rules on sensitive personal data ask for
   a published privacy policy and a named grievance officer. The privacy policy gives a mailbox, not a name.
3. **A representative in the EU and the UK.** A company outside them that offers a service to people there may have
   to appoint one. None is appointed or named.
4. **Gemini: use a paid key.** On the free tier Google may use what is sent, recordings included, and people at Google
   may read it (`docs/INTEGRATIONS.md#gemini`). Confirm billing is on for the key in use. The privacy policy and the
   sub-processor list warn readers about free keys; once yours is paid, a lawyer may let you say so plainly instead.
5. **Google's terms for each account.** Accept the data-processing terms for Google Cloud, Identity Platform and the
   Gemini API. Read what Google keeps (the 48-hour copy of recordings; any abuse-monitoring logs). Where a customer
   needs data kept in one region, set `VERTEX_LOCATION` to a region: the default, `global`, lets Google choose.
6. **Google sign-in for Calendar and Drive.** The Drive scope is one Google calls restricted. Before the public can
   connect, Google must verify the app, and Google's API user-data policy asks for a "Limited Use" statement in the
   privacy policy. The draft has none: add Google's required wording during verification.
7. **The landing page's footage.** Stock video of real people, under the Pexels License
   (`frontend/public/media/CREDITS.md`, read on 14 November 2026). It allows commercial use. It forbids implying that
   the people shown endorse the product. Confirm that the page does not, or bring forward the custom shoot
   (`docs/landing/PRODUCTION.md`).
8. **Self-hosted recordings never expire** unless `RECORDING_RETENTION_DAYS` is set. The notice says so. Set a period
   you can defend.
9. **Name your email provider** in the sub-processor list if `SMTP_HOST` is set. A personal mailbox is a poor sender
   for a business.
10. **A written incident procedure**, before promising 72 hours.
11. **Turn `LEGAL_ENFORCE` on** once the web app that shows the prompt is deployed.
12. **Insurance, company registration and tax details** for invoices. None of it is in the product.

## Publishing a new version

A document's version is a date. Changing its words in any way that matters means a new one.

1. Edit the text in `frontend/src/legal/` (one file per document). Write the document's `changes`: two or three plain
   sentences on what is different. People who accepted the version before are shown them when they are asked again;
   without `changes` they are shown the "In brief" list instead.
2. Change that document's date in `VERSIONS`, in **both** `frontend/src/legal/documents.ts` and
   `backend/app/legal.py`. A backend test fails when they differ. Acceptable Use follows the terms' date.
3. Run `node docs/legal/build.mjs`. It checks the documents and rewrites `docs/legal/*.md`.
   `node docs/legal/build.mjs --check` fails when those files are stale: run it before a release.
4. If the change is about data, update `docs/legal/DATA_INVENTORY.md` in the same change.
5. Deploy the API and the web app together.

Then everyone is asked once to accept the new terms, privacy policy or recording notice, and an admin is asked to
accept a new data-processing agreement. Earlier acceptances stay on record under their own version. Nobody is
prompted about a new sub-processor list: tell admins yourself.

Git history keeps the old text. A record of acceptance names a version, not the words: when a customer accepts the
data-processing agreement, keep that version's `docs/legal/DPA.md` with the downloaded record.

To send a lawyer or a customer a copy with your company's details filled in, without committing them:

```
LEGAL_OPERATOR_NAME="…" LEGAL_OPERATOR_ADDRESS="…" LEGAL_CONTACT_EMAIL="…" LEGAL_PRIVACY_EMAIL="…" \
LEGAL_GOVERNING_LAW="the laws of …" LEGAL_COURTS="the courts of …" \
node docs/legal/build.mjs --filled --out /path/to/a/folder
```

When the lawyer has signed the texts off, set `LEGAL_REVIEW.status` to `"reviewed"` in
`frontend/src/legal/documents.ts` and regenerate. Until then every published page carries a small "Draft" mark and the
line "A first draft, not yet reviewed by a lawyer."

A new document, or a new address for one, is four edits that a test holds together
(`backend/tests/test_legal_pages.py`): `frontend/src/legal/paths.ts`, `frontend/public/boot-theme.js`,
`frontend/nginx.conf.template` and, for the recording notice, `NOTICE_PATH` in `backend/app/services/recordings.py`.
