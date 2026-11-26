/** Sub-processors: who else handles workspace data, by the way the service is run. Backed by section 12 of
 *  docs/legal/DATA_INVENTORY.md. Legal entity names are for the founders' lawyer to fill in (docs/LEGAL.md). */
import { type DocumentBody, caution, note, p, section, service, table } from "./blocks";

const COLUMNS = ["Provider", "What it does", "What it receives", "Where"];

export const subprocessors: DocumentBody = {
  id: "subprocessors",
  title: "Sub-processors",
  lede: "The other companies that handle your organisation's data so that Welcome Wave can run.",
  audience: "Your organisation's admins, and whoever looks after privacy there",
  acceptedBy: "none",
  summary: [
    "One company does almost all of it: Google. It hosts the service, signs people in and runs the AI models.",
    "The list depends on how the service is run: on Google Cloud, or on a single server. The first section says which applies to the service you are reading this on.",
    "An email provider and LangSmith tracing are used only when the operator turns them on.",
    "GitHub, Google Calendar and Calendly are your organisation's own services, not our sub-processors.",
    "We tell your admins at least 30 days before a new provider starts handling your data.",
  ],
  sections: [
    section("this-service", "Which of these apply",
      p("Sections 1 and 2 are two ways of running the service: one of them applies. Section 3 lists providers that are used only when the operator turns them on."),
      service("hosting", "ai", "email", "tracing"),
    ),
    section("google-cloud", "1. When the service runs on Google Cloud",
      table(COLUMNS, [
        ["Google Cloud", "Hosts the service: the application, the database, the file store, the job queue, secrets, logs and monitoring", "All workspace data", "The application, the database, the file store and secrets: one region the operator chooses. The default is Mumbai, India (asia-south1). The job queue, logs and monitoring are not tied to that region. A queued job carries ids, file paths and, for a pull request, its description and its author's GitHub username."],
        ["Google Identity Platform (Firebase Authentication)", "Signs people in", "Email address, password hash, display name, the link to a Google account, sign-in times", "Google's global infrastructure"],
        ["Google Vertex AI (Gemini models)", "Runs the AI: answers, notes, plans, reviews and search", "The text of the code and documents your organisation connects, all of it, to build the search index. For each task: passages of them, pull request changes, transcripts after redaction, questions, and the onboarding records an agent drafts from. Recordings, read from the file store.", "Any Google region, unless the operator pins one. Search indexing runs in the service's own region unless the operator sets another."],
      ]),
      note("If the operator sets a Gemini API key on Google Cloud, the Gemini API in section 2 is used in place of Vertex AI."),
    ),
    section("single-server", "2. When the service runs on a single server",
      table(COLUMNS, [
        ["The server's hosting company, if the operator rents the server", "Provides the machine", "Everything on it: the database and the files", "Where the server is"],
        ["Google Identity Platform (Firebase Authentication)", "Signs people in", "Email address, password hash, display name, the link to a Google account, sign-in times", "Google's global infrastructure"],
        ["Google Gemini API", "Runs the AI, when the operator has set a key", "The same content as Vertex AI above. Each recording is uploaded to Google for the analysis and deleted there when it ends, within 48 hours at most.", "Google's choice of region"],
      ]),
      caution("On the Gemini API's free tier, Google's terms let Google use what is sent to improve its products, and let people at Google read it. On the paid tier they do not. An installation that holds real data must use a paid key.", "Free and paid keys"),
    ),
    section("optional", "3. Only when the operator turns them on",
      table(COLUMNS, [
        ["An email (SMTP) provider of the operator's choice", "Sends invitations, pre-boarding links, the experts' digest and weekly summaries", "The recipient's address and the message. The digest carries a count and a link, never a question.", "The provider's"],
        ["LangSmith (LangChain, Inc.)", "Traces agent runs for debugging. Off by default.", "Each traced run's prompts and outputs: passages of code and documents, transcripts after redaction, questions. LangSmith keeps them for its own period; a deletion in Welcome Wave does not remove them.", "United States, or the European Union if the operator chooses that endpoint"],
      ]),
      p("Without an email provider, nothing is emailed, and the product shows each link for a person to share. Without tracing, nothing goes to LangSmith."),
    ),
    section("your-services", "4. Services your organisation connects itself",
      p("These act for your organisation under its own agreements with them. They are not our sub-processors, but data moves between them and Welcome Wave."),
      table(["Service", "What moves"], [
        ["GitHub", "Welcome Wave reads the repositories your organisation connects: code, pull requests, issues and check results. It writes nothing."],
        ["Google Calendar, Meet and Drive", "Welcome Wave creates calendar events for KT sessions, with a title, an agenda, a time and the attendees' email addresses. Google emails the invitations. Welcome Wave reads a session's recording from Drive. The permission Google is asked for (drive.readonly, with calendar.events) technically allows reading any file in the connecting account's Drive; the software opens only the recording attached to a session's calendar event. A dedicated account for the connection keeps that narrow."],
        ["Calendly", "A booking link carries the booker's name and email to Calendly. Calendly sends bookings back, with the names and emails of the people in them."],
      ]),
    ),
    section("changes", "5. Changes to this list",
      p("Each version carries its date. Before a new provider starts handling workspace data, we publish a new version and tell your organisation's admins, at least 30 days ahead. {{doc:dpa}} says how your organisation can object."),
      p("Questions: {{privacy_email}}."),
    ),
  ],
};
