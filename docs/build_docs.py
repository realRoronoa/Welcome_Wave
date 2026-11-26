"""Builds docs/PRD_Vijayabaskar_Aman_Nautiyal.pdf (BRD + PRD + TRD). Run: python3 docs/build_docs.py"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, ListFlowable, ListItem,
                                NextPageTemplate, PageBreak, PageTemplate, Paragraph, Preformatted, Spacer, Table,
                                TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

HERE = Path(__file__).resolve().parent
D = HERE / "diagrams"
SHOTS = Path("/tmp/claude-0/-home-claude/cab4921c-401b-590e-a6d8-f7dc165a9f76/scratchpad/shots")
OUT = HERE / "PRD_Vijayabaskar_Aman_Nautiyal.pdf"

F = "/usr/share/fonts/truetype/crosextra/"
pdfmetrics.registerFont(TTFont("Body", F + "Carlito-Regular.ttf"))
pdfmetrics.registerFont(TTFont("Body-B", F + "Carlito-Bold.ttf"))
pdfmetrics.registerFont(TTFont("Body-I", F + "Carlito-Italic.ttf"))
pdfmetrics.registerFont(TTFont("Mono", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"))
pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-B", italic="Body-I", boldItalic="Body-B")

INK, SOFT, STEEL, LINE = colors.HexColor("#1d2742"), colors.HexColor("#55607a"), colors.HexColor("#edf0f5"), colors.HexColor("#d6dce6")
TEAL, TEALW, AMBER, AMBERW, RED = colors.HexColor("#0b7a75"), colors.HexColor("#e3f2f0"), colors.HexColor("#b7791f"), colors.HexColor("#fff4e0"), colors.HexColor("#c8403a")

S = {
    "body": ParagraphStyle("body", fontName="Body", fontSize=10.5, leading=14.5, textColor=INK, spaceAfter=6),
    "small": ParagraphStyle("small", fontName="Body", fontSize=9, leading=12, textColor=SOFT),
    "cell": ParagraphStyle("cell", fontName="Body", fontSize=9, leading=11.5, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="Body-B", fontSize=9, leading=11.5, textColor=INK),
    "head": ParagraphStyle("head", fontName="Body-B", fontSize=9, leading=11.5, textColor=colors.white),
    "part": ParagraphStyle("part", fontName="Body-B", fontSize=26, leading=30, textColor=INK, spaceAfter=10),
    "h1": ParagraphStyle("h1", fontName="Body-B", fontSize=16, leading=20, textColor=INK, spaceBefore=10, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName="Body-B", fontSize=12, leading=15, textColor=INK, spaceBefore=8, spaceAfter=4),
    "lead": ParagraphStyle("lead", fontName="Body", fontSize=12, leading=17, textColor=INK, spaceAfter=8),
    "caption": ParagraphStyle("caption", fontName="Body-I", fontSize=9, leading=12, textColor=SOFT, spaceBefore=3, spaceAfter=10),
    "code": ParagraphStyle("code", fontName="Mono", fontSize=7.6, leading=9.6, textColor=INK, backColor=STEEL, borderPadding=6, spaceBefore=4, spaceAfter=10),
    "callout": ParagraphStyle("callout", fontName="Body", fontSize=10.5, leading=14.5, textColor=INK, backColor=TEALW, borderPadding=(8, 10, 8, 10), borderColor=TEAL, borderWidth=0, spaceBefore=6, spaceAfter=12, leftIndent=4),
    "top": ParagraphStyle("top", fontName="Body-B", fontSize=16, leading=20, textColor=INK, spaceBefore=10, spaceAfter=6),
    "plain_h1": ParagraphStyle("plain_h1", fontName="Body-B", fontSize=16, leading=20, textColor=INK, spaceBefore=10, spaceAfter=6),
    "toc1": ParagraphStyle("toc1", fontName="Body-B", fontSize=11, leading=16, textColor=INK, leftIndent=0),
    "toc2": ParagraphStyle("toc2", fontName="Body", fontSize=10, leading=13.5, textColor=INK, leftIndent=14),
}
W = A4[0] - 40 * mm  # text width


class Doc(BaseDocTemplate):
    def __init__(self, path):
        super().__init__(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm, bottomMargin=20 * mm,
                         title="Welcome Wave: BRD, PRD and TRD", author="Vijayabaskar, Aman Nautiyal",
                         subject="Agentic, multimodal onboarding platform")
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
        self.addPageTemplates([PageTemplate("cover", [frame], onPage=self.cover_bg), PageTemplate("body", [frame], onPage=self.chrome)])

    def cover_bg(self, c, d):
        c.saveState()
        c.setFillColor(INK)
        c.rect(0, A4[1] - 95 * mm, A4[0], 95 * mm, stroke=0, fill=1)
        c.setFillColor(TEAL)
        c.rect(0, A4[1] - 97 * mm, A4[0], 2 * mm, stroke=0, fill=1)
        c.restoreState()

    def chrome(self, c, d):
        c.saveState()
        c.setFont("Body", 8.5)
        c.setFillColor(SOFT)
        c.drawString(20 * mm, A4[1] - 13 * mm, "Welcome Wave  |  BRD, PRD and TRD  |  v2.0 agentic")
        c.drawRightString(A4[0] - 20 * mm, A4[1] - 13 * mm, "Vijayabaskar, Aman Nautiyal")
        c.setStrokeColor(LINE)
        c.line(20 * mm, A4[1] - 15 * mm, A4[0] - 20 * mm, A4[1] - 15 * mm)
        c.drawRightString(A4[0] - 20 * mm, 11 * mm, f"{d.page}")
        c.restoreState()

    def afterFlowable(self, f):
        if isinstance(f, Paragraph) and f.style.name in ("h1", "part", "top"):
            level = 1 if f.style.name == "h1" else 0
            key = f"k{id(f)}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(f.getPlainText(), key, level=level, closed=level > 0)
            self.notify("TOCEntry", (level, f.getPlainText(), self.page, key))


story: list = []


def p(text, style="body"):
    story.append(Paragraph(text, S[style]))


def h1(t):
    story.append(CondPageBreak(60 * mm))
    p(t, "h1")


def h2(t):
    story.append(CondPageBreak(30 * mm))
    p(t, "h2")


def bullets(items, style="body"):
    story.append(ListFlowable([ListItem(Paragraph(i, S[style]), leftIndent=12, value="circle") for i in items],
                              bulletType="bullet", start="-", leftIndent=12, bulletFontName="Body", bulletColor=SOFT, spaceAfter=6))


def table(rows, widths, head=True, zebra=True, first_bold=False, highlight=None):
    data = []
    for r_i, row in enumerate(rows):
        cells = []
        for c_i, cell in enumerate(row):
            st = S["head"] if (head and r_i == 0) else (S["cellb"] if first_bold and c_i == 0 else S["cell"])
            cells.append(cell if not isinstance(cell, str) else Paragraph(cell, st))
        data.append(cells)
    t = Table(data, colWidths=[w * W for w in widths], repeatRows=1 if head else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    if head:
        style.append(("BACKGROUND", (0, 0), (-1, 0), INK))
    if zebra:
        for i in range(1 if head else 0, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f6f8fb")))
    for (r, c) in (highlight or []):
        style.append(("BACKGROUND", (0, r), (-1, r), TEALW))
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 8))


def figure(path, caption, width=W, max_h=200 * mm):
    img = Image(str(path))
    ratio = img.imageHeight / img.imageWidth
    w = width
    h = w * ratio
    if h > max_h:
        h, w = max_h, max_h / ratio
    img.drawWidth, img.drawHeight = w, h
    story.append(KeepTogether([img, Paragraph(caption, S["caption"])]))


def callout(text):
    story.append(Paragraph(text, S["callout"]))


# =====================================================================================
# COVER
# =====================================================================================
story.append(Spacer(1, 18 * mm))
story.append(Paragraph("Welcome Wave", ParagraphStyle("ct", fontName="Body-B", fontSize=34, leading=40, textColor=colors.white)))
story.append(Paragraph("An agentic, multimodal onboarding platform for engineering teams",
                       ParagraphStyle("cs", fontName="Body", fontSize=15, leading=20, textColor=colors.HexColor("#dfe5f1"), spaceBefore=6)))
story.append(Spacer(1, 6 * mm))
story.append(Paragraph("Business, Product and Technical Requirements (BRD, PRD, TRD)  |  version 2.0",
                       ParagraphStyle("cv", fontName="Body", fontSize=11, leading=14, textColor=colors.HexColor("#9aa6c2"))))
story.append(Spacer(1, 52 * mm))
story.append(Paragraph("Prepared by", S["small"]))
story.append(Paragraph("Vijayabaskar  and  Aman Nautiyal", ParagraphStyle("names", fontName="Body-B", fontSize=20, leading=26, textColor=INK, spaceBefore=2)))
story.append(Spacer(1, 4 * mm))
story.append(Paragraph("On-Job Training (OJT) project  |  15 October 2026", S["body"]))
story.append(Spacer(1, 14 * mm))
table([
    ["Document", "What it answers", "Reader"],
    ["Part A: BRD", "Why build this, for whom, and how we will know it worked", "Mentor, judges, sponsors"],
    ["Part B: PRD", "What the product does: 14 sections, same format as v1", "Whole team, mentor"],
    ["Part C: TRD", "How it is built: architecture, agents, database, APIs, deployment", "Developers, mentor"],
], [0.2, 0.55, 0.25])
p("Version history: v1.0 described a GenAI document question-answering assistant. v2.0 (this document) rewrites it as an "
  "agentic, multimodal system. The code that matches this document is delivered with it (welcome-wave.zip).", "small")
story.append(NextPageTemplate("body"))
story.append(PageBreak())

toc = TableOfContents()
toc.levelStyles = [S["toc1"], S["toc2"]]
p("Contents", "plain_h1")
story.append(toc)
story.append(PageBreak())

# =====================================================================================
# WHAT CHANGED
# =====================================================================================
p("What changed: GenAI app to agentic AI app", "top")
p("Our mentor asked us to rewrite the BRD, PRD and TRD because the project is no longer a GenAI app. A GenAI app waits for a "
  "question and generates an answer. An agentic app has goals, plans, uses tools, takes actions across several steps, and "
  "asks a human before anything consequential. The table shows every place the design changed.")
table([
    ["Aspect", "v1: GenAI assistant", "v2: Agentic, multimodal platform"],
    ["Trigger", "A user types a question", "Events: a repo push, a finished KT recording, a new hire starting, a pull request opening, or a question"],
    ["What the AI does", "Retrieves and generates one answer", "Six agents plan, call tools, chain into each other and act: write notes, link video to code, propose tasks, review PRs"],
    ["Inputs", "Uploaded documents (text)", "Code, documents, speech and screen video from KT calls (multimodal)"],
    ["Control", "None needed", "LangGraph Supervisor routes, retries, checkpoints, pauses for approval, escalates failures"],
    ["Human role", "Reads the answer", "Human-in-the-loop: the manager approves plans and decides reviews; seniors answer what the agent cannot"],
    ["Memory", "Vector store of documents", "Postgres + pgvector: code, docs, meetings, KT links, tasks, reviews, agent run history"],
    ["Proof it works", "Manual testing", "Evaluation in CI (retrieval, honesty, citations, review accuracy, prompt injection) and LangSmith traces"],
    ["Deployment", "Single server", "Cloud Run + GKE Autopilot workers + Pub/Sub on Google Cloud, Terraform, Cloud Build"],
], [0.17, 0.3, 0.53], first_bold=True)
callout("<b>The headline feature, in one sentence:</b> during a recorded KT call the Meeting agent watches the shared screen, "
        "recognises the file and function being shown, and links that exact moment of the recording to those exact lines of "
        "code. Every line of code then remembers who explained it, and the system knows which code nobody has explained yet.")
story.append(PageBreak())

# =====================================================================================
# PART A - BRD
# =====================================================================================
p("Part A. Business Requirements (BRD)", "part")
p("Why we are building Welcome Wave, who it is for, and the measurable outcomes that decide whether it succeeded.", "lead")

h1("A1. Executive summary")
p("New engineers take months to become productive because project knowledge lives in seniors' heads, unrecorded KT calls "
  "and scattered documents. Every new hire pulls a senior engineer back into the same explanations. Welcome Wave records the "
  "KT once, turns it into searchable, code-linked memory, and uses agents to run the rest of the first 30 days: a plan, a real "
  "first pull request, answers with sources, an evidence-based review, and a manager view of who is stuck.")
p("What makes it different from ChatGPT, Notion AI or GitHub Copilot: those tools wait to be asked and never see your "
  "meetings. Welcome Wave joins the call, links what was shown to the code, acts on what it learned, and keeps a human in "
  "charge of every decision that matters.")

h1("A2. Business problem")
table([
    ["Pain", "Who feels it", "Cost today"],
    ["Training depends on people", "Senior engineers", "Days per hire repeating the same KT"],
    ["KT knowledge disappears", "New hires", "Nobody rewatches an hour of video to find two minutes"],
    ["Documents are scattered and stale", "New hires", "Answers live in code, chats and people's heads"],
    ["Tasks are assigned from memory", "Managers", "No clear path; first PR comes late"],
    ["Questions interrupt people", "Whole team", "Shy new hires stay stuck; seniors lose focus"],
    ["Progress is invisible", "Managers", "Problems surface only when a deadline is missed"],
], [0.36, 0.22, 0.42], first_bold=True)
p("<b>Problem statement.</b> Onboarding new engineers is slow because project knowledge is locked in people's heads, "
  "scattered documents and unrecorded meetings. Teams lose senior engineering time to repeated training, and new hires take "
  "months to become productive.")

h1("A3. Target market")
bullets([
    "<b>Product teams</b> onboarding one to five engineers a quarter, where a senior's time is the bottleneck.",
    "<b>Large employers onboarding fresher cohorts</b> (campus hires joining in batches), where the same KT is repeated for "
    "dozens of people and managers cannot see who is stuck. This is the larger market and the strongest story for funding and placements.",
    "<b>Open-source projects</b> onboarding new contributors (for example through LFX mentorships): public repositories make the "
    "demo and the evaluation reproducible.",
])

h1("A4. Business objectives and success metrics")
table([
    ["Objective", "Metric", "Target for the demo"],
    ["New engineers ship sooner", "Days from joining to first merged PR, with vs without Welcome Wave", "Measured in a 4-person study; report honestly"],
    ["Seniors repeat themselves less", "Questions answered without a human / all questions", "70% or more on the test repo"],
    ["Answers can be trusted", "Answers whose citation points to a real file line, page or meeting moment", "95% or more"],
    ["KT is reusable", "Screen moments linked to the correct file", "80% or more on 3 recorded mock KT calls"],
    ["Managers see trouble early", "Blockers flagged before a deadline is missed", "Every overdue or rejected task shows on the dashboard"],
    ["It is safe to run", "Agent actions taken without the required approval", "Zero"],
], [0.26, 0.44, 0.30], first_bold=True)

h1("A5. Stakeholders")
table([
    ["Stakeholder", "Interest", "Responsibility"],
    ["New joiner", "Learn fast without bothering people", "Uses chat, does tasks, flags wrong answers"],
    ["Manager", "Predictable ramp-up, visibility", "Connects repo, approves plans, decides reviews"],
    ["Senior engineer", "Explain once, not ten times", "Runs KT calls, answers routed questions, confirms weak KT links"],
    ["Mentor / evaluators", "A deployable, well-engineered system", "Review architecture, security, testing, delivery"],
    ["Vijayabaskar, Aman Nautiyal", "Build and run it", "Own agents, platform, deployment, fixes"],
], [0.25, 0.35, 0.40], first_bold=True)

h1("A6. Business requirements")
table([
    ["ID", "Requirement", "Priority"],
    ["BR-1", "A company can connect a code repository and documents, read-only, in under 10 minutes", "Must"],
    ["BR-2", "KT calls happen inside the product and are recorded with a visible consent notice", "Must"],
    ["BR-3", "Every KT recording becomes notes, action items and moments linked to code, without manual work", "Must"],
    ["BR-4", "New hires get answers that always show their source, and the system says when it does not know", "Must"],
    ["BR-5", "Each new hire gets a plan and a real first PR task; a manager approves before it is assigned", "Must"],
    ["BR-6", "Pull requests get an evidence-based review recommendation; a manager makes the final decision", "Must"],
    ["BR-7", "Managers see progress, blockers, repeated questions and unexplained parts of the codebase", "Must"],
    ["BR-8", "One company can never see another company's data", "Must"],
    ["BR-9", "Unanswered questions reach the right senior, and their answer helps the next person", "Should"],
    ["BR-10", "Cost stays within a student cloud budget: scale to zero when idle, alerts on spend", "Should"],
], [0.1, 0.75, 0.15], first_bold=True)

h1("A7. Scope")
table([
    ["In scope for November", "Out of scope (later)"],
    ["Web app for new hire, manager and senior roles", "Mobile app"],
    ["GitHub repositories; PDF, Word and Markdown documents", "GitLab, Bitbucket, Confluence, Jira import"],
    ["In-app KT calls (LiveKit) and recording", "Joining Zoom, Teams or Google Meet calls"],
    ["Notes, KT-to-code links, coverage map, cited chat", "Live captions and a live in-call assistant"],
    ["Plans, first-PR task, PR reviews with approval", "Agents writing to GitHub (comments, labels, merges)"],
    ["English-language calls", "Other languages"],
], [0.5, 0.5])

h1("A8. Assumptions, constraints and business risks")
bullets([
    "<b>Time:</b> about three weeks to a clean release branch (first week of November). Scope is cut in a fixed order if a sprint slips (see PRD section 12).",
    "<b>Budget:</b> student cloud credits. Everything scales to zero when idle; a billing budget alerts at 50% and 90%.",
    "<b>Access:</b> GCP project with billing and Vertex AI, a GitHub App on an org we control, LiveKit Cloud and LangSmith keys are requested in Sprint 0.",
    "<b>Privacy:</b> recordings contain people's voices and screens. Consent notice in every call; personal data redacted from transcripts; raw recordings deleted after 30 days.",
    "<b>Trust:</b> AI can be wrong. Every answer shows sources, low confidence is handed to a human, and agents only recommend.",
])
story.append(PageBreak())

# =====================================================================================
# PART B - PRD (14 sections)
# =====================================================================================
p("Part B. Product Requirements (PRD)", "part")
p("Same 14-section format as the v1 PRD, rewritten for the agentic, multimodal product.", "lead")

h1("1. Product overview")
p("Welcome Wave is a web platform where a team connects its repository, holds KT calls, and lets six AI agents handle the "
  "repetitive parts of onboarding. A Supervisor agent receives every event and hands it to one specialist agent: Ingestion "
  "(reads code and documents), Project Q&amp;A (answers with sources), Meeting (watches recordings, writes notes, links moments to "
  "code), Task (plans work, picks a first PR, tracks status) and Review (checks pull requests with evidence). Managers approve "
  "plans and decide reviews; agents never act on GitHub.")

h1("2. Problem statement")
p("Onboarding is slow because knowledge is locked in people, unrecorded meetings and scattered documents. KT calls are the "
  "richest source of knowledge a team produces, yet after a week they are effectively lost: nobody can search an hour of video "
  "for the two minutes that explain the webhook handler. Existing AI tools answer questions from text but never see those calls, "
  "never plan the work, and never check it.")

h1("3. Goals and non-goals")
h2("Goals")
bullets([
    "Make every KT call searchable and linked to the code it explained.",
    "Get a new engineer to an opened, reviewed first pull request within their first two weeks.",
    "Answer project questions with sources, and say \"not enough information\" instead of guessing.",
    "Give managers a single place to approve agent proposals and see onboarding health.",
])
h2("Non-goals")
bullets([
    "Training our own language model. \"Our own agentic AI\" means our agents, tools, memory, guardrails and evaluation, built on Gemini.",
    "Replacing seniors or managers. Agents recommend; people decide.",
    "Running on two clouds. One cloud (Google Cloud) done well beats two done halfway.",
])

h1("4. Users and personas")
table([
    ["Persona", "Goal", "Main screens", "Can do"],
    ["Vijay, new hire (day 3)", "Understand the code and ship a first PR", "Ask the project, Code and KT, My tasks, KT calls", "Ask, watch clips, update task status, answer understanding questions"],
    ["Priya, engineering manager", "Ramp people up predictably", "Approvals, Onboarding health, Team tasks", "Connect repo, upload docs, start onboarding, approve plans, decide reviews"],
    ["Ravi, senior engineer", "Explain once, not ten times", "KT calls, expert inbox", "Run recorded KT calls, answer routed questions, confirm KT links"],
    ["Developers (us)", "Keep agents correct and running", "LangSmith, Cloud Monitoring, agent runs", "Change prompts and tools, deploy, fix"],
], [0.2, 0.22, 0.27, 0.31], first_bold=True)

h1("5. User journey: the golden path")
p("Every feature exists to serve one loop. The demo, the automated end-to-end test and the sprint gates all follow it.")
figure(D / "golden.png", "Figure 1. Golden path. Amber steps pause for the manager. The dashed line is what makes it a product rather than a chatbot: gaps found decide the next KT call.")
bullets([
    "<b>1-2.</b> Priya connects the GitHub repository (read-only GitHub App) and uploads two documents. The Ingestion agent indexes code at function level, documents by page, and reads CODEOWNERS.",
    "<b>3.</b> Ravi runs \"KT #1: How validation works\" inside the app, sharing his screen. A banner tells everyone it is recorded.",
    "<b>4.</b> When the call ends, the Meeting agent watches the recording, writes notes, and links 00:55 to <font name='Mono' size='8.5'>engine/validate.py</font>, function <font name='Mono' size='8.5'>check_labels</font>. The coverage map now shows the webhook module was never explained.",
    "<b>5.</b> The Task agent proposes Vijay's plan: read the unexplained webhook module, the meeting's action items, and a first PR from a labelled starter issue. Priya unticks one task and approves the rest.",
    "<b>6.</b> Vijay asks \"Which function reports missing labels?\" The answer cites the code lines and offers the 00:55 clip where Ravi explained it.",
    "<b>7.</b> Vijay opens a PR on branch <font name='Mono' size='8.5'>task-&lt;id&gt;</font>. The Review agent finds a missing test (automatic check) and a raise that breaks the engine's contract (with the exact line as evidence) and asks three understanding questions. Priya requests changes.",
    "<b>8.</b> The dashboard shows Vijay's progress, the review outcome and the remaining KT gaps.",
])

h1("6. Features and functional requirements")
p("Priority follows the time we have. Must-haves make the golden path work end to end; differentiators are what a chat assistant "
  "cannot do; stretch items start only after the golden-path test passes.")
table([
    ["ID", "Feature", "Agent", "Priority"],
    ["F-1", "Connect a GitHub repository (read-only App) and upload PDF, Word or Markdown documents", "Ingestion", "Must"],
    ["F-2", "Incremental re-index on every push; answers citing changed code are marked stale", "Ingestion", "Must"],
    ["F-3", "Cited project chat: every answer links to a file line range, document page or meeting moment", "Q&amp;A", "Must"],
    ["F-4", "\"Not enough information\" when unsure, and the question is routed to the code owner", "Q&amp;A", "Must"],
    ["F-5", "In-app KT video call with recording and a visible consent banner", "Meeting", "Must"],
    ["F-6", "Meeting notes: summary, decisions, action items, new terms, redacted transcript", "Meeting", "Must"],
    ["F-7", "<b>KT-to-Code Linking</b>: moments in the recording linked to the files and functions shown on screen", "Meeting", "Must"],
    ["F-8", "Code view: each line shows whether and where it was explained; one click plays the moment", "UI", "Must"],
    ["F-9", "30-day plan and a real first-PR task, proposed by the agent and approved by the manager", "Task", "Must"],
    ["F-10", "Task status follows GitHub: PR opened means submitted, merged means done", "Task", "Must"],
    ["F-11", "PR review with file-and-line evidence and a recommendation; manager decides", "Review", "Must"],
    ["F-12", "Manager dashboard: progress, overdue tasks, changes requested, repeated questions", "Supervisor", "Must"],
    ["F-13", "KT coverage map: minutes of KT per module; modules never explained become tasks and KT agenda items", "Meeting + Task", "Differentiator"],
    ["F-14", "Expert inbox: routed questions answered by a senior become verified answers for the next person", "Q&amp;A", "Differentiator"],
    ["F-15", "Understanding check: three questions about the new hire's own PR", "Review", "Differentiator"],
    ["F-16", "Live in-call assistant (private sidebar during the call)", "Meeting", "Stretch"],
    ["F-17", "Cohort mode: one plan for a batch of freshers, shared-blocker detection", "Task", "Stretch"],
    ["F-18", "Slack or Teams bot for the chat", "Q&amp;A", "Stretch"],
], [0.07, 0.65, 0.14, 0.14], first_bold=True)

h1("7. Headline feature: KT-to-Code Linking")
p("<b>What the user sees.</b> In the code view a teal mark sits beside every line that was explained on a recorded call. "
  "Selecting it opens the recording at that second. In chat, an answer that cites code also offers the clip. On the dashboard, "
  "the coverage map lists modules with zero minutes of KT; the Task agent turns those into reading tasks and the next KT agenda.")
p("<b>Why it is the game changer.</b> It is the one capability that needs all three of our ingredients at once: video "
  "understanding (multimodal), knowledge of the codebase (retrieval), and acting on the result without being asked (agentic). "
  "General chat tools cannot do it because they never see the meetings. And it gets more valuable with every call a company "
  "records, which is the kind of accumulating advantage evaluators look for.")
figure(D / "ktlink.png", "Figure 2. How a moment in a recording becomes a link to code. Matching goes from most to least certain and every link keeps its method and confidence.")
table([
    ["Requirement", "Acceptance criterion"],
    ["Link moments to files", "When a file path is visible on screen, the link points to that file with confidence 0.85 or more"],
    ["Link to the function", "When the function name is also visible, the link carries its line range"],
    ["Handle partial screen text", "A basename like validate.py resolves to engine/validate.py when unique; ambiguous names get low confidence"],
    ["Never overclaim", "Matches made only by meaning are capped at 0.75 and labelled \"matched by meaning\" in the UI"],
    ["One clip, not ten", "Consecutive moments on the same code merge into one clip"],
    ["Stay honest after changes", "If linked code changes later, the clip shows \"code changed after this call\""],
    ["Measured", "80% of screen moments in 3 labelled mock KT calls link to the correct file"],
], [0.3, 0.7], first_bold=True)

h1("8. Agent design and autonomy")
p("Six agents, one Supervisor. Agents never call each other; every hop goes through the Supervisor so each run is traced, "
  "checkpointed and testable. The table defines how much freedom each agent has.")
table([
    ["Action", "Who decides", "How it is enforced"],
    ["Answer a chat question", "Agent alone, with sources", "Citations must point at retrieved blocks; invented ones are dropped"],
    ["Hand a question to a senior", "Agent alone", "Below confidence threshold or no valid citation"],
    ["Write meeting notes and KT links", "Agent alone", "Transcripts are redacted before storage"],
    ["Read a repository or document", "Agent alone, after the manager connects it", "GitHub App has read-only scopes; client has no write methods"],
    ["Assign tasks", "Agent proposes, manager approves or edits", "LangGraph interrupt: the run pauses until the decision"],
    ["Review submitted work", "Agent recommends, manager decides", "LangGraph interrupt; findings without evidence are discarded"],
    ["Change anything in GitHub", "Not allowed", "No write capability exists in the code"],
], [0.28, 0.3, 0.42], first_bold=True)

h1("9. Non-functional requirements")
table([
    ["Area", "Requirement"],
    ["Performance", "Chat p95 under 8 s at 50 concurrent users; a 30-minute KT call processed within 10 minutes"],
    ["Scalability", "Stateless Cloud Run API; GKE workers scale on queue backlog, to zero when idle"],
    ["Availability", "Multi-zone Cloud Run and GKE Autopilot; uptime check alerts within minutes; daily backups with point-in-time recovery"],
    ["Resiliency", "At-least-once events with dead-letter topics; idempotent handlers; resumable agent runs; fallback model"],
    ["Security", "Identity Platform login, roles, row-level security per company, least-privilege service accounts, secrets in Secret Manager"],
    ["Privacy", "Consent banner; redaction of emails, phone numbers and keys; 30-day retention for raw recordings"],
    ["Observability", "LangSmith trace for every agent run; JSON logs with request IDs; dashboards for errors, latency, queue depth, LLM spend"],
    ["Accessibility", "Keyboard navigation, visible focus, readable contrast, reduced motion respected"],
], [0.2, 0.8], first_bold=True)

h1("10. UX and screens")
p("Screens below are from the working build in demo mode (sample repository, one recorded KT call, offline model).")
figure(SHOTS / "code.png", "Figure 3. Code and KT: teal marks show explained lines; the right column lists the KT clips for this file. The file tree shows minutes of KT per module and modules never explained.", max_h=110 * mm)
figure(SHOTS / "chat.png", "Figure 4. Ask the project: every answer shows its sources; when cited code was explained on a call, the clip is offered.", max_h=110 * mm)
figure(SHOTS / "meeting.png", "Figure 5. KT call notes: summary, decisions, action items with timestamps, code shown on screen, transcript.", max_h=110 * mm)
figure(SHOTS / "approvals.png", "Figure 6. Approvals: the Task agent's plan waits for the manager, who can untick tasks before assigning.", max_h=100 * mm)
figure(SHOTS / "dashboard.png", "Figure 7. Onboarding health: progress per new hire and KT coverage by module.", max_h=110 * mm)

h1("11. Success metrics and evaluation")
p("The demo ends on numbers we measured, not claims. Automated evaluations run in CI (the build fails below target) and every "
  "run is traced in LangSmith so a wrong answer can be traced to the exact retrieval and prompt.")
table([
    ["What we test", "Test set", "Metric", "Target"],
    ["Retrieval", "50 questions with known source files on the test repo", "Right file in top 5", "80%"],
    ["Honesty", "10 questions the repo cannot answer", "Says \"not enough information\"", "9 of 10"],
    ["Citations", "Every answer in the golden run", "Citation exists and supports the answer", "95%"],
    ["KT linking", "3 recorded mock KT calls, moments labelled by hand", "Moment linked to correct file", "80%"],
    ["Meeting notes", "Same 3 calls", "Action items found", "80%"],
    ["Review agent", "10 PRs with a planted problem", "Problem flagged with correct evidence", "7 of 10"],
    ["Prompt injection", "10 files and transcripts with hidden instructions", "Agent behaviour unchanged", "10 of 10"],
    ["Speed", "Chat under load (Locust)", "p95 response time", "Under 8 s"],
    ["Real users", "4 classmates, one starter task each, half with the platform", "Time to an opened PR", "Report what we observe"],
], [0.17, 0.38, 0.3, 0.15], first_bold=True)
p("Status today: the evaluation harness runs (<font name='Mono' size='8.5'>python -m eval.run_eval</font>) and scores retrieval "
  "recall@5 = 1.00 on the 6-file sample repository. That number says the harness works, not that the product does: the real "
  "measurement uses the 50-question set on the actual test repository in Sprint 1.", "small")

h1("12. Release plan and timeline")
p("Four short sprints get the golden path running on dev by 30 October and the release branch clean by Wednesday 4 November, "
  "leaving 5 and 6 November as buffer before the first-week-of-November deadline.")
figure(D / "sprints.png", "Figure 8. Sprint plan. Sprint 2 carries the headline feature and is the riskiest.")
table([
    ["Sprint", "Gate (demonstrable at the end)"],
    ["S0, Oct 14-16", "A hello-world build deploys to Cloud Run through Cloud Build; database schema and sign-in work"],
    ["S1, Oct 17-23", "The chat answers the 50-question set with citations on dev; Supervisor traced in LangSmith"],
    ["S2, Oct 24-30", "The full golden path runs end to end on dev, including KT-to-Code links from a real recorded call"],
    ["S3, Oct 31-Nov 4", "Every non-functional requirement checked, load test passed, prod demo rehearsed, release branch clean"],
], [0.2, 0.8], first_bold=True)

BACKLOG = [
    # sprint, story, epic, points, owner, starting point
    ("S0", "Set up GCP projects with Terraform: APIs, IAM, budgets, secrets", "Deployment", 3, "Aman", "Scaffolded"),
    ("S0", "Wire the monorepo into one dev environment; run the demo locally", "Platform", 2, "Vijayabaskar", "Scaffolded"),
    ("S0", "Cloud Build pipeline to Artifact Registry and Cloud Run (dev)", "Deployment", 5, "Aman", "Scaffolded"),
    ("S0", "Cloud SQL + pgvector, schema migration, app role without BYPASSRLS", "Data", 3, "Vijayabaskar", "Scaffolded"),
    ("S0", "Identity Platform sign-in and roles in the web app", "Security", 5, "Vijayabaskar", "Partly"),
    ("S1", "GitHub App (read-only) and webhook receiver with signature check", "Ingestion", 5, "Aman", "Scaffolded"),
    ("S1", "Code chunker: function-level chunks; upgrade to tree-sitter", "Ingestion", 5, "Aman", "Scaffolded"),
    ("S1", "Document upload and page-aware parsing", "Ingestion", 3, "Aman", "Scaffolded"),
    ("S1", "Hybrid search: pgvector + full text, rank fusion, per company", "Q&A", 5, "Vijayabaskar", "Scaffolded"),
    ("S1", "Q&A agent: validated citations, not-enough-information, expert routing", "Q&A", 5, "Vijayabaskar", "Scaffolded"),
    ("S1", "Chat UI with source chips and KT clips", "Frontend", 3, "Vijayabaskar", "Scaffolded"),
    ("S1", "Supervisor graph: routing, retries, Postgres checkpoints, LangSmith", "Agents", 5, "Vijayabaskar", "Scaffolded"),
    ("S1", "Tasks page and task data model", "Tasks", 3, "Aman", "Scaffolded"),
    ("S1", "Golden set of 50 questions on the test repo; evaluation in CI", "Testing", 3, "Both", "Partly"),
    ("S2", "LiveKit call in the app: rooms, tokens, consent banner", "Meeting", 5, "Aman", "Scaffolded"),
    ("S2", "Recording to Cloud Storage and recording-ready webhook", "Meeting", 3, "Aman", "Scaffolded"),
    ("S2", "Meeting worker on GKE: Gemini video segments, redaction, notes", "Meeting", 8, "Aman", "Scaffolded"),
    ("S2", "KT-to-Code linker: path, symbol and meaning matching", "KT linking", 5, "Vijayabaskar", "Scaffolded"),
    ("S2", "Code view with clip marks; notes page with jump-to-moment player", "Frontend", 5, "Vijayabaskar", "Scaffolded"),
    ("S2", "KT coverage map and gap detection", "KT linking", 3, "Aman", "Scaffolded"),
    ("S2", "Task agent: 30-day plan, first-PR pick, approval interrupt", "Tasks", 8, "Vijayabaskar", "Scaffolded"),
    ("S2", "Task status from PR webhooks", "Tasks", 2, "Aman", "Scaffolded"),
    ("S2", "Review agent: evidence validation, rule checks, understanding questions", "Review", 8, "Vijayabaskar", "Scaffolded"),
    ("S2", "Expert inbox and verified answers", "Q&A", 3, "Vijayabaskar", "Partly"),
    ("S2", "Manager dashboard", "Frontend", 3, "Aman", "Scaffolded"),
    ("S3", "Dead letters, retries, idempotency, fallback model verified on GCP", "Resiliency", 3, "Aman", "Partly"),
    ("S3", "Security pass: RLS tests, injection tests, secret scan", "Security", 5, "Vijayabaskar", "Partly"),
    ("S3", "Monitoring dashboard, alerts, uptime checks", "Observability", 3, "Aman", "Partly"),
    ("S3", "Load test with 50 users; autoscaling limits", "Scalability", 3, "Aman", "Partly"),
    ("S3", "End-to-end golden-path test on prod", "Testing", 3, "Vijayabaskar", "Partly"),
    ("S3", "Bug bash and clean release branch", "Bugs", 3, "Both", "New"),
    ("S3", "Demo data, rehearsal, final documents", "Demo", 2, "Both", "Partly"),
]
h2("Jira backlog")
p("Create one epic per area and these stories with Fibonacci story points. \"Scaffolded\" means code for it is already in the "
  "delivered repository and the story is to finish it, connect it to the real service and test it there.")
rows = [["#", "Story", "Epic", "Pts", "Sprint", "Owner", "Start"]]
for i, (sp, st, ep, pts, ow, start) in enumerate(BACKLOG, 1):
    rows.append([str(i), st, ep, str(pts), sp, ow, start])
table(rows, [0.05, 0.47, 0.12, 0.05, 0.07, 0.13, 0.11])
by_sprint, by_owner = {}, {"Vijayabaskar": 0.0, "Aman": 0.0}
for sp, _, _, pts, ow, _ in BACKLOG:
    by_sprint[sp] = by_sprint.get(sp, 0) + pts
    if ow == "Both":
        by_owner["Vijayabaskar"] += pts / 2
        by_owner["Aman"] += pts / 2
    else:
        by_owner[ow] += pts
total = sum(b[3] for b in BACKLOG)
p(f"Total {total} points: " + ", ".join(f"{k} {v}" for k, v in by_sprint.items()) +
  f". Vijayabaskar {by_owner['Vijayabaskar']:.0f} points, Aman {by_owner['Aman']:.0f} points. "
  "If Sprint 1 closes under 40 points, cut in this order: expert inbox, understanding questions, jump-to-moment player. "
  "Never cut KT-to-Code linking; it is the headline.")

h1("13. Risks and mitigations")
table([
    ["Risk", "Likelihood", "Mitigation"],
    ["Building a video call from scratch takes too long", "High if attempted", "Use LiveKit Cloud; the Meeting agent works on its recording"],
    ["Gemini misreads file names on screen", "Medium", "Give the model the repo file list; layered matching; confidence shown; seniors can confirm links"],
    ["Wrong answers or wrong reviews", "Medium", "Citations validated, \"not enough information\", evidence required, manager overrides"],
    ["Prompt injection through code or transcripts", "Medium", "Untrusted data fenced in prompts; rule checks the model cannot override; 10 injection tests"],
    ["Credentials arrive late", "High", "Requested in Sprint 0; demo mode runs with no keys so work never blocks"],
    ["Preview model changes before demo", "Medium", "Pin a generally available model ID; fallback model configured"],
    ["Cloud cost overrun", "Low", "Scale to zero, budget alerts, 30-day recording deletion"],
    ["Too much scope for three weeks", "High", "Fixed cut order; stretch work starts only after the golden-path test passes"],
], [0.36, 0.16, 0.48], first_bold=True)

h1("14. Open questions and dependencies")
table([
    ["Question", "Owner", "Needed by"],
    ["Keep the name Welcome Wave, or rename now that the product does far more than greet?", "Both", "Oct 17"],
    ["Which open-source repository is the test project (we propose Kyverno or a smaller one we both know)?", "Both", "Oct 16"],
    ["Which agent-management tool did our teacher mean? LangGraph is the answer we can defend either way", "Vijayabaskar", "Oct 17"],
    ["Confirm the generally available Gemini model IDs in Vertex AI Model Garden", "Aman", "Oct 17"],
    ["GCP billing, Vertex AI quota, GitHub App, LiveKit and LangSmith access granted", "Both", "Oct 16"],
], [0.62, 0.18, 0.2], first_bold=True)
story.append(PageBreak())

# =====================================================================================
# PART C - TRD
# =====================================================================================
p("Part C. Technical Requirements (TRD)", "part")
p("How Welcome Wave is built. Everything here matches the delivered code: file names refer to the repository in welcome-wave.zip.", "lead")

h1("C1. Architecture overview")
figure(D / "architecture.png", "Figure 9. Deployment architecture on Google Cloud. Request/response work runs on Cloud Run; long jobs run as GKE workers fed by Pub/Sub.")
p("This table is our answer to the mentor's services form: exactly which service, on which platform, for what.")
table([
    ["GCP service", "What runs there", "Why this one"],
    ["Cloud Run: ww-api", "FastAPI: auth, chat, approvals, webhooks; runs the Supervisor for chat and approval resumes", "Request/response, scales to zero, every deploy is a revision we can roll back"],
    ["Cloud Run: ww-web", "React app behind nginx, forwards /api", "Static site plus proxy, no servers to manage"],
    ["GKE Autopilot", "meeting-worker, ingestion-worker, review-worker (one image, three deployments)", "Long, heavy jobs (video, repo parsing); KEDA scales on queue backlog, to zero"],
    ["Cloud SQL PostgreSQL 16 + pgvector", "All app data, embeddings (HNSW), LangGraph checkpoints", "One database instead of three; row-level security per company"],
    ["Cloud Storage", "Recordings, uploaded documents", "LiveKit uploads directly; lifecycle rule deletes recordings after 30 days"],
    ["Pub/Sub", "Queues: ingestion, meetings, reviews, each with a dead-letter topic", "Decouples workers; retries and dead letters built in"],
    ["Vertex AI", "Gemini (text, audio, video) and text embeddings", "Native video input with time offsets; same IAM and billing"],
    ["Identity Platform", "Sign-in; roles stored in our users table", "Managed auth with verified ID tokens"],
    ["Secret Manager", "GitHub App key, webhook secret, LiveKit and LangSmith keys, database URL", "No secrets in code or images"],
    ["Artifact Registry + Cloud Build", "Images; test, evaluate, build, deploy on every merge", "The CI/CD the mentor asked for"],
    ["Cloud Logging, Monitoring, Trace", "JSON logs, dashboards, alerts, uptime checks", "One place for system health"],
    ["Sensitive Data Protection", "Optional stronger redaction of transcripts", "Same interface as our regex redactor"],
    ["Outside GCP", "LiveKit Cloud (calls, recording), LangSmith (traces, evals), GitHub (code)", "Proven services; we do not rebuild them"],
], [0.24, 0.42, 0.34], first_bold=True)
p("Why not AWS as well: two clouds double credentials, IAM, networking and deployments for no user benefit. The design maps "
  "service for service (Cloud Run to ECS, GKE to EKS, Cloud SQL to RDS, Pub/Sub to SNS and SQS) if we ever need it.")

h1("C2. Agent architecture")
figure(D / "agents.png", "Figure 10. The Supervisor as a LangGraph state graph (backend/app/agents/supervisor/graph.py). Amber nodes are interrupts that wait for the manager.")
bullets([
    "<b>Rules first, LLM second.</b> Events are routed by type with a plain rule table; this is faster, cheaper and testable. Only free-text chat goes to the Q&amp;A agent.",
    "<b>Chaining.</b> A finished meeting with action items flows straight into the Task agent in the same run.",
    "<b>Approval is a paused graph.</b> The approval node calls LangGraph <font name='Mono' size='8.5'>interrupt()</font>. The run is checkpointed in Postgres (PostgresSaver) and resumes with <font name='Mono' size='8.5'>Command(resume=decision)</font> when the manager clicks, even days later. The approvals row stores the thread ID.",
    "<b>Failure handling.</b> Every agent node is wrapped: up to 3 attempts with exponential backoff, then the run is recorded as failed and the Escalate node notifies the manager. A Gemini call also retries and falls back to a second model.",
    "<b>Tool allowlist.</b> Agents receive one <font name='Mono' size='8.5'>Deps</font> object (app/agents/core/deps.py) and nothing else; the repository client exposes read methods only.",
])

h1("C3. Agent specifications")
table([
    ["Agent", "Trigger", "Tools and inputs", "Output", "Guardrails"],
    ["Supervisor", "Every event", "Routing table, checkpointer", "One agent run, chained runs, approvals", "Unknown events escalate; retries; escalation"],
    ["Ingestion", "repo.connected, repo.pushed, document.uploaded", "GitHub tarball and file reads, chunker, embeddings", "Chunks with path, lines, symbol, commit; CODEOWNERS", "Read-only; skips vendored and large files; idempotent chunk IDs"],
    ["Project Q&amp;A", "chat.question", "Hybrid search, KT links, CODEOWNERS", "Answer, citations, KT clips or a routed expert request", "Invented citations dropped; confidence and similarity thresholds"],
    ["Meeting", "meeting.recording_ready", "Gemini video (10-minute clips via offsets), repo file list, redactor, KT linker", "Notes, transcript, screen moments, action items, KT links", "Untrusted-data prompt; redaction before storage"],
    ["Task", "onboarding.started, after a meeting; pr.*", "Coverage map, action items, starter issues, existing tasks", "Proposed tasks and first PR; task status updates", "Invented paths and issues removed; manager approval required"],
    ["Review", "pr.opened, pr.updated", "PR diff, CI checks, project guidelines", "Recommendation, findings, three questions", "Findings need a real changed line and quoted evidence; rule checks override the model"],
], [0.12, 0.17, 0.25, 0.23, 0.23], first_bold=True)

h1("C4. Multimodal pipeline and the KT linker")
p("<b>Meeting processing</b> (app/agents/specialists/note_taker/agent.py). The recording stays in Cloud Storage; Gemini reads it in 10-minute clips "
  "using video start and end offsets, so no video is copied or cut. For each clip the model returns utterances with speakers, "
  "screen moments (time, description, visible file paths and function names), decisions, action items and new terms, with "
  "times relative to the clip, which the agent converts to meeting time. The prompt includes the repository's file list so "
  "partial screen text resolves to real paths.")
p("<b>KT linker</b> (app/services/kt_linker.py) matches each screen moment in three layers and stops at the first that succeeds:")
table([
    ["Layer", "Signal", "Confidence", "Example"],
    ["1. Path", "A path or file name visible on screen, matched by suffix against repo paths", "0.85 to 0.97 (0.55 if ambiguous)", "\"validate.py\" resolves to engine/validate.py"],
    ["1b. Path + symbol", "Path and a function name both visible", "Path score + 0.02, with line range", "check_labels, lines 20-22"],
    ["2. Symbol", "Only a function or class name visible", "0.88 if unique, 0.6 if not", "handle_admission resolves to webhooks/server.py"],
    ["3. Meaning", "Screen description + speech within 30 s, vector search over code", "Similarity, capped at 0.75", "\"code that adds default labels\" resolves to engine/mutate.py"],
], [0.15, 0.4, 0.22, 0.23], first_bold=True)
p("Consecutive moments on the same code are merged into one clip. Coverage (app/services/coverage.py) groups files into modules "
  "(first two folders), sums linked minutes per module with confidence 0.5 or more, and lists modules with zero as gaps.")
p("<b>Code and documents.</b> Code is chunked at function and class boundaries with exact line numbers (regex boundaries today, "
  "tree-sitter in Sprint 1 without changing the chunk contract). Documents keep page numbers. Identifiers are split "
  "(validateResource becomes \"validate resource\") so keyword search finds them.")

h1("C5. Data design")
p("PostgreSQL 16 on Cloud SQL with pgvector. Full DDL: backend/db/migrations/001_init.sql. Every business table carries "
  "<font name='Mono' size='8.5'>org_id</font> and is protected by row-level security; the API sets "
  "<font name='Mono' size='8.5'>app.org_id</font> per transaction, so a faulty query still cannot read another company's rows.")
figure(D / "er.png", "Figure 11. Entity-relationship diagram (main tables). Teal: knowledge and KT links. Amber: human-in-the-loop records.", max_h=205 * mm)
table([
    ["Table", "Purpose", "Key indexes and constraints"],
    ["chunks", "Every searchable piece of code, document, meeting transcript or verified answer, with its exact source in ref", "HNSW on embedding (cosine), GIN on tsv, (project_id, path), (project_id, symbol); deterministic hash ID"],
    ["kt_links", "Which moment of which call explained which code", "(project_id, file_path), chunk_id; confidence 0-1 check; method enum"],
    ["meetings, transcript_segments, screen_moments, action_items, meeting_notes", "Everything the Meeting agent produced", "(meeting_id, start_s); full result kept as JSON for the notes page"],
    ["tasks", "Onboarding work: kind, status, origin, due date, linked issue and PR", "(onboarding_id, status); status enum mirrors the state machine"],
    ["approvals", "The human-in-the-loop queue; thread_id resumes the LangGraph run", "Partial index on pending"],
    ["reviews", "Recommendation, findings with evidence, questions, manager decision", "Unique (task_id, head_sha): a commit is reviewed once"],
    ["chat_messages, expert_requests", "Answers with citations and clips; routed questions and verified answers", "GIN on cited_chunk_ids, used to mark answers stale"],
    ["agent_runs, processed_events, audit_log", "Operations: run history, idempotency keys, who decided what", "processed_events primary key = dedupe key"],
], [0.24, 0.42, 0.34], first_bold=True)
p("Two SECURITY DEFINER functions (auth_lookup, project_org) are the only lookups that run before the tenant is known: login, "
  "and a worker receiving an event. LangGraph creates its own checkpoint tables at start-up.", "small")

h1("C6. API design")
table([
    ["Method and path", "Who", "What it does"],
    ["GET /api/me", "Any", "Current user and role"],
    ["GET /api/projects/{id}", "Member", "Project, repositories, members, onboardings"],
    ["POST /api/projects/{id}/repositories", "Manager", "Connect a repo; publishes repo.connected"],
    ["POST /api/projects/{id}/documents", "Manager, expert", "Upload a document to Cloud Storage; publishes document.uploaded"],
    ["POST /api/projects/{id}/onboardings", "Manager", "Start onboarding; publishes onboarding.started"],
    ["POST /api/chat", "Any", "Ask a question; returns answer, citations, KT clips"],
    ["GET /api/code/files, /code/file, /code/explanations", "Any", "File list with clip counts; file lines; KT clips for a file"],
    ["GET /api/coverage", "Any", "Minutes of KT per module and the gaps"],
    ["POST /api/meetings, /meetings/{id}/join, /record", "Any; record: manager, expert", "Create a call, LiveKit token, start recording"],
    ["GET /api/meetings, /meetings/{id}/notes, /recording", "Any", "Calls, notes, a short-lived signed URL for playback"],
    ["GET /api/tasks; PATCH /api/tasks/{id}", "Any; owner", "Tasks by role; allowed status transitions only"],
    ["GET /api/approvals; POST /approvals/{id}/decide", "Manager", "Pending decisions; approve, edit or reject; resumes the paused run"],
    ["GET /api/reviews/{id}", "Any", "Review with findings and questions"],
    ["GET, POST /api/experts/requests", "Expert, manager", "Routed questions; answering creates a verified answer"],
    ["GET /api/dashboard, /runs; GET /api/notifications", "Manager; any", "Onboarding health; agent runs; notifications"],
    ["POST /webhooks/github", "GitHub", "HMAC SHA-256 verified; push and pull_request become events"],
    ["POST /webhooks/livekit", "LiveKit", "Signed; egress_ended becomes meeting.recording_ready"],
], [0.38, 0.17, 0.45], first_bold=True)

h1("C7. Events")
table([
    ["Event", "Published by", "Queue", "Dedupe key"],
    ["repo.connected", "API (connect repo)", "ingestion", "connect:{repo}:{date}"],
    ["repo.pushed", "GitHub webhook (default branch only)", "ingestion", "push:{after sha}"],
    ["document.uploaded", "API (upload)", "ingestion", "doc:{document}"],
    ["meeting.recording_ready", "LiveKit webhook", "meetings", "rec:{meeting}"],
    ["onboarding.started", "API (start onboarding)", "reviews", "ob:{onboarding}"],
    ["pr.opened, pr.updated", "GitHub webhook", "reviews", "pr:{number}:{head sha}"],
    ["pr.merged", "GitHub webhook", "reviews", "merged:{number}"],
    ["chat.question", "API (synchronous, no queue)", "none", "none"],
], [0.24, 0.32, 0.14, 0.3], first_bold=True)
p("Delivery is at-least-once. A duplicate key in processed_events is skipped; a failure is nacked and retried with backoff; "
  "after 5 attempts the message moves to the queue's dead-letter topic, which raises an alert.")

story.append(CondPageBreak(150 * mm))
h1("C8. Folder structure")
story.append(Preformatted("""welcome-wave/
|-- backend/
|   |-- app/
|   |   |-- agents/        supervisor/     graph.py: LangGraph graph: route, chain, interrupt, escalate
|   |   |                  specialists/    one package per agent (agent.py): librarian, note_taker,
|   |   |                                  guide, planner, scout, reviewer, coach, keeper
|   |   |                  core/           llm.py: Gemini (Vertex AI), FakeLLM (tests), OfflineLLM (demo);
|   |   |                                  runtime.py, registry.py, prompts.py, schemas.py, deps.py
|   |   |                  tools/          the read-only tools the agents call
|   |   |-- services/      kt_linker.py    the headline feature
|   |   |                  coverage.py, chunking.py, diff.py, codeowners.py, redaction.py,
|   |   |                  embeddings.py, dashboard.py, livekit.py
|   |   |-- adapters/      memory.py (demo, tests), postgres.py, github.py, gcp.py
|   |   |-- api/           routes.py, webhooks.py, auth.py
|   |   |-- ports.py       interfaces the agents depend on
|   |   |-- container.py   wiring for MODE=demo or MODE=gcp
|   |   |-- seed.py        demo team, repo and recorded KT call
|   |   `-- main.py        FastAPI app, JSON logging, request IDs
|   |-- workers/worker.py  Pub/Sub consumer for GKE (WORKER_QUEUE=meetings|ingestion|reviews)
|   |-- db/migrations/     001_init.sql   schema, indexes, row-level security
|   |-- eval/              golden_set.jsonl, run_eval.py
|   |-- load/              locustfile.py
|   |-- sample_repo/       small policy engine used by the demo and tests
|   |-- tests/             test_services.py, test_agents.py, test_golden_path.py, test_api.py
|   `-- Dockerfile, requirements.txt, pyproject.toml, .env.example
|-- frontend/
|   |-- src/pages/         Chat, Code, Meetings, Tasks, Approvals, Dashboard
|   |-- src/               App.tsx, api.ts, styles.css
|   `-- Dockerfile, nginx.conf.template, vite.config.ts
|-- infra/
|   |-- terraform/main.tf  APIs, service accounts, Cloud SQL, GCS, Pub/Sub + dead letters,
|   |                      GKE Autopilot, Artifact Registry, secrets, uptime check, alerts
|   `-- k8s/workers.yaml   three worker deployments + KEDA scaling
|-- docs/                  this document, diagrams and the script that builds it
|-- cloudbuild.yaml        test, evaluate, build, deploy
`-- docker-compose.yml     local demo; optional Postgres + pgvector""", S["code"]))

h1("C9. Security")
table([
    ["Threat", "Control", "Where"],
    ["Another company's data", "org_id on every row, row-level security forced, app role without BYPASSRLS; demo-mode checks in the API", "001_init.sql, adapters/postgres.py, api/auth.py"],
    ["Stolen or forged login", "Identity Platform ID tokens verified on every request; roles from our database", "api/auth.py"],
    ["Forged webhooks", "GitHub HMAC SHA-256 with constant-time compare; LiveKit signed webhooks", "adapters/github.py, services/livekit.py"],
    ["Agents changing code", "Read-only GitHub App scopes and a client with no write methods", "adapters/github.py"],
    ["Prompt injection", "Untrusted content fenced and labelled as data; rule checks override the model; tests", "agents/core/prompts.py, agents/specialists/reviewer/review.py, tests"],
    ["Secrets leaking", "Secret Manager, Workload Identity (no key files), redaction of keys in transcripts", "terraform, k8s, services/redaction.py"],
    ["Personal data in recordings", "Consent banner, redaction, 30-day lifecycle on raw recordings, private bucket", "Meetings page, terraform"],
    ["Over-privileged services", "Separate service accounts for API and workers with minimal roles", "terraform/main.tf"],
], [0.22, 0.48, 0.3], first_bold=True)

h1("C10. Reliability, scalability and availability")
bullets([
    "<b>Scale:</b> the API is stateless (max 10 instances, 40 concurrent requests each). Workers scale from zero on Pub/Sub backlog: one meeting per pod, review worker keeps one warm pod so reviews start within seconds.",
    "<b>Idempotency:</b> deterministic chunk IDs, unique review per commit, dedupe keys for every event.",
    "<b>Resumability:</b> LangGraph checkpoints in Postgres let a crashed or paused run continue from the last step.",
    "<b>Graceful degradation:</b> model failure leads to retry, then the fallback model, then \"not enough information\" or an escalation, never a crash.",
    "<b>Availability:</b> Cloud Run and GKE Autopilot spread across zones; Cloud SQL backups with point-in-time recovery, regional (failover) in prod; an uptime check on /health.",
])

h1("C11. Observability")
bullets([
    "LangSmith traces every LangGraph run: inputs, each node, tool calls, model calls, outputs, latency and cost.",
    "agent_runs table records every agent execution with status, shown to managers on the runs page.",
    "Structured JSON logs with a request ID, path, status and latency; Cloud Logging parses them directly.",
    "Cloud Monitoring dashboard: error rate, p95 latency, queue depth per subscription, dead letters, LLM spend per day; alerts on dead letters and uptime.",
])

h1("C12. Testing and CI/CD")
table([
    ["Level", "What", "Status"],
    ["Unit", "Chunking, code owners, diff parsing, redaction, KT linker, coverage", "Passing (tests/test_services.py)"],
    ["Agent", "Citation validation, expert routing, evidence validation, rule findings, prompt injection", "Passing (tests/test_agents.py)"],
    ["Golden path", "Repo, meeting with KT links, plan with approval interrupt, chat with clip, PR review with decision, escalation", "Passing (tests/test_golden_path.py)"],
    ["API", "Demo-mode HTTP: code view, coverage, chat, approvals, roles, webhook signature", "Passing (tests/test_api.py)"],
    ["Evaluation", "Retrieval recall@5; honesty and citations with a real model", "Harness runs; real set in Sprint 1"],
    ["Integration", "Postgres adapter, GitHub App, LiveKit, Pub/Sub, Vertex AI against real services", "Written, not yet run against GCP (Sprints 1-2)"],
    ["Load", "Locust, 50 users on /api/chat", "Script ready; run in Sprint 3"],
], [0.15, 0.55, 0.3], first_bold=True)
p("All 24 automated tests pass today in demo mode. Cloud Build runs, in order: lint and tests, evaluation (fails the build below "
  "target), image builds, push to Artifact Registry, deploy the API and web app to Cloud Run, apply the worker manifests to GKE. "
  "Branch protection on main and release/*; prod deploys from a version tag.")

h1("C13. Environments and credentials")
table([
    ["Environment", "Purpose", "Data"],
    ["demo (local)", "Develop and present without any keys", "In memory, offline model, sample repo, seeded KT call"],
    ["dev (GCP)", "Every merge to main", "Test repository, mock KT calls"],
    ["prod (GCP)", "Demo day", "Same, regional database, min instances 1 during the demo"],
], [0.2, 0.4, 0.4], first_bold=True)
p("Credentials to request now: GCP project with billing and Vertex AI enabled (Gemini quota checked); a GitHub App on an "
  "organisation we control, installed on a fork of the test repository; LiveKit Cloud project keys; LangSmith API key; Jira "
  "project access for both of us.")

h1("C14. Ownership")
table([
    ["Area", "Owner"],
    ["Agent brain: Supervisor, Q&amp;A, Task and Review agents, KT linker, chat and code view, security pass", "Vijayabaskar"],
    ["Platform: Terraform, Cloud Build, GKE workers, Ingestion and Meeting pipelines, LiveKit, coverage map, dashboard, monitoring", "Aman Nautiyal"],
    ["Evaluation sets, bug bash, demo rehearsal, documents", "Both"],
], [0.75, 0.25])
p("Each of us owns one deployment end to end (Vijayabaskar the Cloud Run API and agents, Aman the GKE workers), so both of us "
  "can explain the whole system in an interview.")

h1("Appendix: five-minute demo script")
bullets([
    "Priya connects the test repository and uploads two documents; the code map appears.",
    "Ravi runs a short KT call while sharing his screen, then ends it.",
    "Notes appear; the code view shows teal marks on the functions Ravi showed; the coverage map flags a module nobody explained.",
    "The Task agent's plan appears in Approvals; Priya unticks one task and approves the rest.",
    "Vijay asks two questions: one answered with a code citation and the KT clip, one routed to the code owner.",
    "Vijay opens a prepared PR; the Review agent flags a missing test and a contract-breaking line with evidence; Priya requests changes.",
    "Show the LangSmith trace of the whole chain and the Cloud Monitoring dashboard; close on the evaluation numbers.",
])


def build():
    doc = Doc(OUT)
    doc.multiBuild(story)
    print(OUT)


if __name__ == "__main__":
    build()
