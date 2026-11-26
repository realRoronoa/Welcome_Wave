/** Acceptable use. Part of the Terms of Service (it carries their version). A first draft for the founders' lawyer. */
import { type DocumentBody, p, section, ul } from "./blocks";

export const acceptableUse: DocumentBody = {
  id: "acceptable_use",
  title: "Acceptable Use",
  lede: "The rules for using Welcome Wave. Part of the Terms of Service.",
  audience: "Everyone who signs in",
  acceptedBy: "none",
  summary: [
    "Use Welcome Wave for your organisation's onboarding and knowledge transfer.",
    "Add only what you have the right to add, and record only people who were told and agreed.",
    "Do not attack the service, go around its limits, or try to reach another organisation's data.",
  ],
  sections: [
    section("do", "1. Do",
      ul(
        "Use the service for your organisation's own onboarding and knowledge transfer.",
        "Keep your sign-in to yourself.",
        "Tell everyone in a session before it is recorded, and get their agreement.",
        "Check an AI answer against its sources before you act on it.",
        "Report a security problem to {{contact_email}} as soon as you find it.",
      ),
    ),
    section("do-not", "2. Do not",
      ul(
        "Add code, documents or recordings that you or your organisation have no right to share.",
        "Record someone without telling them, or add a recording that someone in it refused.",
        "Add health records, payment card numbers, government identity numbers or other sensitive personal data. The service is not built to protect them.",
        "Add malware, or anything unlawful.",
        "Probe, scan or overload the service, go around its rate limits or access controls, or try to reach data of an organisation you do not belong to.",
        "Plant instructions in code, documents or recordings to make the AI agents break their rules or reveal other people's data.",
        "Use the service to watch colleagues in secret, or make an AI output the only basis for a decision that seriously affects a person.",
        "Resell the service or let people outside your organisation use your account.",
        "Copy or reverse-engineer the service, except where the law says you may.",
        "Use the service where sanctions or export laws forbid it, or if you are under 18.",
      ),
    ),
    section("secrets", "3. Secrets in what you add",
      p("Keep passwords and keys out of what you add. Text taken from a recording is scanned before it is stored: email addresses, phone numbers, common key formats and password-like values are replaced with labels. The scan follows patterns and can miss things. Code and documents are stored as you provide them."),
    ),
    section("breaking", "4. If these rules are broken",
      p("We may remove the content or suspend the account involved, as the {{doc:terms}} set out. We tell your organisation's admins first when we reasonably can."),
    ),
  ],
};
