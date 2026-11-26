# Legal acceptances: who accepted what, and when

The record-keeping behind the legal documents. The documents' words are in the web app
(`frontend/src/legal/documents.ts`, also written out to [docs/legal/](../legal/)); they are first drafts for the founders'
lawyer, not legal advice. This page covers what the server does: versions, the operator's details, the record of each
acceptance, and the routes. Code: `backend/app/legal.py`, `backend/app/api/legal.py`, `backend/db/migrations/019_legal.sql`.

## Versions

Each document has a version, a date: `terms`, `privacy`, `dpa`, `recording`, `subprocessors`. The table is in two places,
`VERSIONS` in `backend/app/legal.py` and `VERSIONS` in `frontend/src/legal/documents.ts`, and
`tests/test_legal.py::test_the_versions_are_the_ones_the_web_app_shows` fails when they differ. A person is shown one and
the server records the other, so they must be the same.

**Changing a document** means changing its date in both places, in the same release. Everyone is then asked to accept it
again, once. Earlier acceptances stay on record. A version names a text only if that text can be found again: keep each
published version in version control (a tag per change is enough).

| Document | Who accepts it | How |
|---|---|---|
| Terms of Service, Privacy Policy | each person | at sign-up or on joining, in the same request; later versions in the app |
| Recording Notice | each person | acknowledged in the app, once per version (or with sign-up, if the page shows it) |
| Data Processing Agreement | an admin, for the organisation | Settings, once per version |
| Sub-processors | nobody | published and dated; a change is a new version |

## The operator's details

The documents name the company that runs the service. Those facts are settings, never text in the code:

| Setting | In `GET /api/config` → `legal` |
|---|---|
| `LEGAL_OPERATOR_NAME` | `operator` |
| `LEGAL_OPERATOR_ADDRESS` | `address` |
| `LEGAL_CONTACT_EMAIL` | `contact_email` |
| `LEGAL_PRIVACY_EMAIL` | `privacy_email` |
| `LEGAL_GOVERNING_LAW` | `governing_law` |
| `LEGAL_COURTS` | `courts` |

- None is needed to start the server. One that is not set is `null`, and the pages say "the company that operates this
  service" instead: never a made-up name.
- A left-over placeholder (`[COMPANY]`, `<your company>`, `TODO`, `changeme`) counts as not set, and so does a contact
  address that is not an email address.
- `./scripts/selfhost.sh check` (`python -m scripts.check_integrations`) lists each one as `set` or `not set`, with what the
  documents say meanwhile. A missing one never fails the check.
- Self-hosted: `backend/selfhost.env` (quote values that have spaces). Google Cloud: set them once on the `ww-api` service
  (`gcloud run services update ww-api --update-env-vars ...`); the deploy updates its own variables and keeps these.
  Kubernetes: add them to the overlay's `settings/config.env`.

## The record of an acceptance

One row in `legal_acceptances` per acceptance:

| Column | What |
|---|---|
| `org_id`, `user_id` | The organisation and the person. For `dpa`, the admin who accepted for the organisation. Ids only: no name, no email |
| `document`, `version` | Which document, and which version of it |
| `accepted_at` | When (the server's clock) |
| `method` | `signup`, `invitation`, `prompt` (asked in the app), or `organisation` (the DPA) |
| `ip_prefix` | The network the click came from: IPv4 cut to /24, IPv6 to /48. Never the full address |
| `user_agent` | The browser's own description, cut to 200 characters |

**Why so little.** The person is signed in with a verified login when they accept, so the account already says who. The
network says "from the customer's office" if an acceptance is ever disputed, without singling out a device or a home. The
full address would add little and is personal data, so it is not stored.

**Append-only.** The application's database role can add and read rows and can never change or remove one: the migration
revokes `UPDATE`, `DELETE` and `TRUNCATE`, the migrator revokes them again on every run, and it refuses to finish if the
role holds any (`migrate.APPEND_ONLY_TABLES`). One acceptance per person, document and version; one DPA acceptance per
organisation and version (the first admin's stands; a second click answers the same record).

**Tenancy.** `org_id` under forced row-level security, like every organisation table.

**Audit.** `legal.accepted` (the person; which documents and versions, and how) and `legal.dpa_accepted` (the organisation;
the version). An acceptance that was already recorded is not audited twice.

## What happens to the record

| Event | The record |
|---|---|
| A person is erased (`POST /api/org/people/erase`) | **Kept**, under the erased identity. Their name and email are replaced on the user row; the acceptance names them by id only, so it still shows that an acceptance happened and when, and no longer who. The same as their onboarding records and audit entries. The network and browser description stay with the row |
| A person leaves or is deactivated | Kept |
| Organisation export (`GET /api/org/export`) | Included, as `legal_acceptances` |
| Organisation deleted (`DELETE /api/org`) | **Deleted** with everything else, through `delete_org_legal()`, which removes only the current organisation's rows and only after the organisation row is gone |
| Time passes | Nothing. No retention period removes acceptances earlier |

The DPA's downloadable record (`GET /api/org/legal/dpa/record`) is a text file: organisation, version, time, the admin's
name and email as they are now, their user id, the operator's name if set, the network. It records the acceptance, not the
agreement's text. If that admin is later erased, the file says "Erased person" and keeps the id, version and time: an
organisation that wants the name on file downloads the record when it accepts.

## Being asked

`GET /api/legal/status` answers `needs`: the personal documents whose current version the caller has not accepted. The web
app asks at sign-in and posts `POST /api/legal/accept`. So:

- **Existing members**, who joined before the documents existed, are asked once at their next sign-in. Nothing else changes
  for them.
- **New accounts.** `POST /api/signup` and `POST /api/invitations/accept` take `accept: ["terms", "privacy"]` and record it
  in the same request. Without both they answer 422 `legal_required` and create nothing. If the account is made and the
  acceptance then fails to save, the person is in and is asked at their next sign-in.
- **The demo** seeds everyone's acceptance and the organisation's DPA, so it opens on the product. Someone who joins the
  demo from an invitation is asked like anyone else.
- **Signing in, reading the documents, exporting and deleting** never depend on acceptance.

**`LEGAL_ENFORCE`** (off by default). Off: the web app asks, and the API refuses nothing. On: the API also answers 403
`{code: "legal_required", needs, versions}` on signed-in routes until the caller has accepted the current terms and privacy
policy. Still open: `/api/me`, `/api/me/leave`, `/api/legal/*`, `/api/notifications`, `/api/live`, and for admins
`/api/org/export`, `DELETE /api/org` and `/api/org/legal/*`. The recording notice is not part of this gate. Turn it on only
when the deployed web app shows the prompt, or people are refused with nothing to click.

**In the web app.** `frontend/src/consent/LegalGate.tsx` reads the status after sign-in and shows the consent screen
(`ConsentScreen.tsx`) to anyone whose `needs` holds a required document; a later 403 `legal_required` brings it back. The
sign-up screens send `accept` (`frontend/src/auth/Agree.tsx`). The recording notice is acknowledged on the KT pages
(`frontend/src/consent/RecordingNotice.tsx`), and the organisation's agreement in Settings, Organisation
(`frontend/src/pages/settings/Legal.tsx`). If the status cannot be read and `LEGAL_ENFORCE` is off, the workspace opens
and the question is asked at the next visit.

## Routes

| Route | Who | |
|---|---|---|
| `GET /api/config` | public | `legal: {operator, address, contact_email, privacy_email, governing_law, courts, versions, enforced}` |
| `GET /api/legal/status` | any member | `{needs, accepted: [{document, version, at}], versions, required, enforced}` |
| `POST /api/legal/accept` | any member, executives included | `{documents, versions?}` → the status plus `recorded`. 422 `unknown_document`, `dpa_is_organisation`; 409 `version_changed` |
| `GET /api/org/legal` | admin | `{dpa: {version, accepted, accepted_at, accepted_by, previous}, versions, operator}` |
| `POST /api/org/legal/dpa` | admin | `{version?}` → the same plus `recorded`. 409 `version_changed` |
| `GET /api/org/legal/dpa/record` | admin | the text record; `?version=`; 404 `dpa_not_accepted` |
| `POST /api/signup`, `POST /api/invitations/accept` | a verified login | `accept` is required; `"recording"` may be added, and at sign-up `"dpa"` |

`versions` in a request is optional: the versions the person was shown. If one is no longer current the answer is 409, so
nobody accepts a version they did not see.

For other code: `legal.has_acknowledged(c, user)` says whether a person has acknowledged the current recording notice.

## Decide with your lawyer

Nothing here is legal advice. These were chosen as conventional defaults and are yours to change:

1. **Is a click enough for the DPA?** The product records an admin's click, with version, time and person. Some customers
   will want a signed copy; the downloadable record is not a signature.
2. **Who may accept the DPA.** Any admin of the organisation can. The product does not check that they have authority to
   bind their company.
3. **Evidence after erasure.** An erased person's acceptance stays, by id, with the network and browser description. Keeping
   it is the usual position (it is evidence of a contract); confirm it for the places you sell in.
4. **Evidence after deletion.** Deleting an organisation deletes its acceptances and its audit log. If you want to keep proof
   that a former customer accepted the terms, that copy has to be kept outside the product, and the privacy policy has to
   say so.
5. **What is stored about the click.** A network prefix and a browser description. Storing less (nothing) or more (the full
   address) are both defensible; this is the middle.
6. **Enforcing acceptance.** `LEGAL_ENFORCE` is off. Whether continued use without a click counts as acceptance of a new
   version is a legal question; turning it on makes the click a condition of use.
7. **The operator's details**, the governing law and the courts: the six settings above.

## Tests

`tests/test_legal.py` (versions, settings, the routes, sign-up and joining, the DPA, erasure, export, deletion,
enforcement) and `tests/test_legal_postgres.py` (the table as `ww_app`: append-only, row-level security, export and
deletion, the routes over HTTP; run through `pg_tests.sh`). The permission matrix covers the routes for every role.
