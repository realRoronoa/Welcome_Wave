/** "On this service": how the service a reader is on is set up, in the documents' own plain words.
 *
 *  The documents are written once for every way Welcome Wave can be run, so some statements branch: "on Google Cloud … / on
 *  a single server …", "only when the operator turns it on", "the number of days the operator has set". The server knows
 *  which branch is true of itself and says so in GET /api/config (`legal.deployment`, backend/app/legal.py). These lines put
 *  that beside the text, so a reader is never left to guess. No React and no DOM: Legal.tsx renders the lines.
 *
 *  A line states only what the server reported. A fact it did not report gives no line. */
import type { Deployment, ServiceTopic } from "./types";

/** Google Cloud regions a reader is more likely to know by place than by code. Any other region is shown by its code. */
const PLACES: Record<string, string> = {
  "asia-south1": "Mumbai, India",
  "asia-south2": "Delhi, India",
  "asia-southeast1": "Singapore",
  "europe-west1": "Belgium",
  "europe-west2": "London, United Kingdom",
  "europe-west3": "Frankfurt, Germany",
  "europe-west4": "the Netherlands",
  "us-central1": "Iowa, United States",
  "us-east1": "South Carolina, United States",
  "us-east4": "Northern Virginia, United States",
};

const place = (region: string) => (PLACES[region] ? `${PLACES[region]} (${region})` : region);
const days = (n: number) => (n === 1 ? "1 day" : `${n} days`);

const TRACES = {
  us: "in the United States",
  eu: "in the European Union",
  other: "at the address the operator has set",
} as const;

function line(topic: ServiceTopic, d: Deployment): string | null {
  const demo = d.hosting === "demo";
  switch (topic) {
    case "hosting":
      if (demo) return "This is a demo. It holds made-up data in memory and forgets it when it restarts.";
      if (d.hosting === "single_server") return "It runs on a single server.";
      return d.region ? `It runs on Google Cloud. The database and the files are in ${place(d.region)}.` : "It runs on Google Cloud.";
    case "ai":
      if (d.ai === "offline") return "The demo answers from a built-in stand-in. Nothing is sent to an AI provider.";
      if (d.ai === "none") return "No AI provider is set up, so the AI features are off and nothing is sent to one.";
      if (d.ai === "gemini_api") return "AI requests go to the Google Gemini API, with the operator's key. Google chooses the region.";
      return !d.ai_location || d.ai_location === "global"
        ? "AI requests go to Google Vertex AI, which may serve them from any Google region."
        : `AI requests go to Google Vertex AI, in ${place(d.ai_location)}.`;
    case "tracing":
      return d.tracing
        ? `Tracing to LangSmith is on. Traces are kept ${TRACES[d.tracing_region ?? "us"]}, for as long as LangSmith keeps them.`
        : "Tracing to LangSmith is off. Nothing is sent to it.";
    case "email":
      return d.email
        ? "An email provider is set up. Invitations and summaries are emailed."
        : "No email provider is set up. Nothing is emailed.";
    case "recordings":
      if (demo || d.hosting !== "single_server" || d.recording_retention_days == null) return null;
      return d.recording_retention_days > 0
        ? `A recording's video file is deleted ${days(d.recording_retention_days)} after it was stored.`
        : "No period is set for recordings. A video file is kept until someone removes it.";
    case "audit":
      return demo || d.audit_retention_days == null ? null : `Audit entries are kept ${days(d.audit_retention_days)}.`;
    case "signin":
      return demo ? null : d.verified_email
        ? "A verified email address is needed to join or create an organisation."
        : "A verified email address is not required to join or create an organisation.";
  }
}

/** The lines for a place in a document, in the order its topics are listed. None when the server said nothing. */
export function serviceLines(topics: readonly ServiceTopic[], d: Deployment | null | undefined): string[] {
  if (!d || typeof d.hosting !== "string") return [];
  const out: string[] = [];
  for (const t of topics) {
    const text = line(t, d);
    if (text && !out.includes(text)) out.push(text);
  }
  return out;
}

/** What each topic is, for a copy that has no server behind it: "In the product, this place says …". */
export const SERVICE_TOPICS: Record<ServiceTopic, string> = {
  hosting: "where it runs",
  ai: "which AI service it uses",
  tracing: "whether tracing is on",
  email: "whether email is on",
  recordings: "how long a recording's video file is kept",
  audit: "how long audit entries are kept",
  signin: "whether a verified email address is required",
};

/** The same, as one sentence for docs/legal/*.md. */
export function serviceNote(topics: readonly ServiceTopic[]): string {
  const names = topics.map((t) => SERVICE_TOPICS[t]);
  const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
  return `In the product, this place says how the service you are reading it on is set up: ${list}.`;
}
