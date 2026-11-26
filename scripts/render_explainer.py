#!/usr/bin/env python3
"""Render the Welcome Wave narrated motion-graphics explainer.

Requires macOS `say`, Pillow, ffmpeg and ffprobe. Final files go in docs/video/;
temporary narration and previews go in dist/ (ignored by git).
The project facts and example are in docs/PROJECT_GUIDE.md. This script owns its
original narration and diagrams so the video can be rebuilt without a cloud editor.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "video"
WORK = ROOT / "dist" / "explainer-work"
VIDEO_SLUG = "welcome-wave-workflow-explainer"
WIDTH, HEIGHT, FPS = 1280, 720, 12
INK = "#0a0a0a"
PAPER = "#fafafa"
TEAL = "#2dd4bf"
MUTED = "#9fa7aa"
PANEL = "#181d1e"
STROKE = "#30393a"
FONT = "/System/Library/Fonts/SFNS.ttf"
MONO = "/System/Library/Fonts/SFNSMono.ttf"

CHAPTERS = [
    {
        "title": "Explain once. Reuse everywhere.", "eyebrow": "THE PROBLEM",
        "narration": [
            "Meet Maya, a new backend engineer facing a large repository, scattered documents, and questions no one has written down.",
            "Every new hire asks similar things, but the best explanations often live only in a senior engineer's head.",
            "Welcome Wave connects those explanations to the exact code they describe.",
            "A senior records a knowledge transfer session once. The next engineer can find the relevant moment, watch it, and learn.",
        ],
        "cards": [
            (80, 210, 315, 260, "MAYA", "New engineer", "Repository\nScattered docs\nRepeated questions"),
            (484, 215, 300, 250, "WELCOME WAVE", "Knowledge travels", "Explain code once\nLink it to source\nReuse it with evidence"),
            (875, 210, 315, 260, "RAVI", "Senior engineer", "Records one walkthrough\nof how the code works"),
        ],
        "arrows": [(400, 335, 475, 335), (789, 335, 865, 335)],
    },
    {
        "title": "A program starts with a person", "eyebrow": "01  ENROL & APPROVE",
        "narration": [
            "Priya, the manager, invites Maya to her team's Welcome Wave project.",
            "She selects a ninety-day program template, start date, mentor Ravi, team, and focus area.",
            "The Planner agent drafts milestones and tasks from that setup.",
            "Its plan waits in Approvals. Priya checks it and decides which tasks Maya receives.",
            "Before day one, Maya gets a pre-boarding link with documents, access requests, a first-week preview, and a machine check.",
        ],
        "cards": [
            (65, 195, 255, 290, "PRIYA", "Manager", "Invites Maya\nChooses template\nNames Ravi as mentor"),
            (370, 195, 255, 290, "PLANNER", "Agent proposal", "30 / 60 / 90 plan\nMilestones\nSuggested tasks"),
            (675, 195, 255, 290, "APPROVAL", "Human decision", "Priya reviews\nand assigns the plan"),
            (980, 195, 235, 290, "MAYA", "Pre-boarding", "Documents\nAccess\nMachine check"),
        ],
        "arrows": [(324, 340, 366, 340), (629, 340, 671, 340), (934, 340, 976, 340)],
    },
    {
        "title": "A recording becomes code knowledge", "eyebrow": "02  RECORD, INDEX, LINK",
        "narration": [
            "Ravi explains engine slash validate dot pie while sharing his screen in a recorded knowledge transfer meeting.",
            "The Librarian reads the connected GitHub repository without write access, splits files into searchable pieces, and indexes them.",
            "The Note-taker analyses the recording's speech and screen moments, writes notes, and redacts transcript text.",
            "Welcome Wave first matches a file path visible on screen, then a visible function name, then meaning if neither is legible.",
            "The result links check privileged in engine slash validate dot pie to Ravi's recording at the relevant time, with a confidence score.",
            "If that code changes later, the explanation can be marked stale.",
        ],
        "cards": [
            (64, 188, 355, 300, "GITHUB / LIBRARIAN", "Read-only repository", "engine/validate.py\n  check_privileged()\n  return violations"),
            (462, 188, 355, 300, "RAVI / NOTE-TAKER", "Recorded KT", "Video + speech + screen\n00:55 — 01:30\n'Why return a list?'"),
            (860, 188, 355, 300, "KT → CODE LINK", "Confidence attached", "Path → symbol → meaning\ncheck_privileged()\nWatch Ravi at 00:55"),
        ],
        "arrows": [(421, 337, 458, 337), (819, 337, 856, 337)],
    },
    {
        "title": "Maya learns what is known", "eyebrow": "03  TOUR, COVERAGE, RISK",
        "narration": [
            "Maya opens the code tour and sees which functions Ravi explained.",
            "A click takes her straight to the matching clip, not to the beginning of a long meeting.",
            "The coverage map shows modules that have no recorded explanation.",
            "Knowledge risk highlights a critical file with only one owner.",
            "The KT planner proposes a session to close that gap. A person chooses whether to book it.",
        ],
        "cards": [
            (65, 198, 355, 282, "CODE TOUR", "Learn in context", "engine/validate.py\n  ▶ Ravi at 00:55\n  What this function does"),
            (462, 198, 355, 282, "KT COVERAGE", "See the gaps", "engine: explained\nwebhooks: needs KT\nNext session suggested"),
            (859, 198, 355, 282, "KNOWLEDGE RISK", "One owner", "Critical file\nOnly one expert knows it\nPropose a KT session"),
        ],
        "arrows": [(421, 340, 458, 340), (819, 340, 855, 340)],
    },
    {
        "title": "Answers show their evidence", "eyebrow": "04  ASK, CITE, ROUTE",
        "narration": [
            "Maya asks why check privileged returns a list instead of raising an exception.",
            "The Guide searches code, documents, recorded moments, and verified answers.",
            "It gives numbered citations and a matching clip, then checks that the claims are supported.",
            "When evidence is weak, the Guide says so and offers to ask a person.",
            "Maya chooses that option. Her mentor Ravi receives the question according to office hours and interruption limits.",
            "Ravi can answer and save a verified answer for the next person.",
        ],
        "cards": [
            (60, 200, 360, 280, "MAYA ASKS", "A real question", "Why return a list\ninstead of raising?"),
            (460, 200, 360, 280, "GUIDE SEARCHES", "Read-only evidence", "[1] Code lines\n[2] KT moment\n[3] Verified answer"),
            (860, 200, 360, 280, "ANSWER OR PERSON", "Citation check", "Cited answer + clip\nor 'ask Ravi?'\nMaya chooses"),
        ],
        "arrows": [(421, 340, 457, 340), (821, 340, 857, 340)],
    },
    {
        "title": "A safe first contribution", "eyebrow": "05  FIRST TASK & REVIEW",
        "narration": [
            "The Scout ranks real first-task candidates using Maya's focus, task size, tests, KT coverage, and ownership risk.",
            "Priya selects a safe issue and Maya receives a brief with first steps.",
            "Maya writes a draft and asks for a private pre-submit review of her diff.",
            "The Reviewer anchors each finding to changed lines, tests, or the relevant KT moment. Only Maya can see this private review.",
            "She fixes the issue and opens a pull request on GitHub herself. Welcome Wave never pushes code to the repository.",
            "The regular review proposes a recommendation; Priya makes the decision in Approvals.",
        ],
        "cards": [
            (65, 200, 255, 280, "SCOUT", "Rank real issues", "Small scope\nTests exist\nExplained code"),
            (365, 200, 255, 280, "PRIYA", "Select task", "Reviews shortlist\nAssigns Maya's\nfirst issue"),
            (665, 200, 255, 280, "MAYA", "Private draft", "Writes change\nChecks diff\nFixes findings"),
            (965, 200, 255, 280, "GITHUB PR", "People decide", "Reviewer proposes\nPriya approves\nor requests changes"),
        ],
        "arrows": [(323, 341, 360, 341), (623, 341, 660, 341), (923, 341, 960, 341)],
    },
    {
        "title": "The program keeps moving", "eyebrow": "06  COACH & 90-DAY PROGRAM",
        "narration": [
            "Onboarding continues across ninety days, with tasks, milestones, access, and check-ins.",
            "The Coach checks stalled work, failed environment checks, and blockers. It nudges Maya when she needs help.",
            "If a blocker persists, escalation moves in stages after business-day delays.",
            "Weekly summaries count progress and link to its evidence without quoting Maya's private questions.",
            "Maya and Priya check in around day thirty, sixty, and ninety. Priya decides the completion sign-off.",
        ],
        "cards": [
            (75, 215, 265, 255, "DAY 0", "Start", "Pre-boarding\nAccess + setup"),
            (365, 215, 265, 255, "DAY 30", "First check-in", "First PR\nEvidence of progress"),
            (655, 215, 265, 255, "DAY 60", "Second check-in", "Coach and mentor\nclear blockers"),
            (945, 215, 265, 255, "DAY 90", "Completion", "Final review\nPriya signs off"),
        ],
        "arrows": [(342, 342, 361, 342), (632, 342, 651, 342), (922, 342, 941, 342)],
    },
    {
        "title": "Knowledge stays current", "eyebrow": "07  KEEPER & HANDOVER",
        "narration": [
            "When code changes, an old recording may no longer describe it accurately.",
            "The Keeper proposes a re-recording or a document edit. A manager approves the proposal, and the repository owner applies edits.",
            "If an expert is leaving, a handover lists their owned files, missing KT, and a successor.",
            "Managers see blockers, coverage, and time to first pull request in reports. Small survey groups are suppressed for privacy.",
            "The point is to keep useful explanations alive, even as a team and its code change.",
        ],
        "cards": [
            (70, 204, 350, 275, "CODE CHANGES", "Old KT may be stale", "A changed function\nneeds a fresh explanation"),
            (465, 204, 350, 275, "KEEPER PROPOSES", "Human approval", "Re-recording\nDoc edit\nHandover"),
            (860, 204, 350, 275, "TEAM RETAINS", "Knowledge that lasts", "Successor knows the file\nCoverage improves\nReports show impact"),
        ],
        "arrows": [(422, 342, 461, 342), (817, 342, 856, 342)],
    },
    {
        "title": "How the system fits together", "eyebrow": "08  ARCHITECTURE & MODES",
        "narration": [
            "Under the hood, a React browser app calls a FastAPI backend.",
            "A LangGraph Supervisor routes each event to a specialist agent, while lifecycle and knowledge services enforce the business rules.",
            "Demo mode uses seeded in-memory data and resets when restarted.",
            "Self-hosted mode uses Postgres with pgvector, Firebase sign-in, and real integrations. Google Cloud runs the same application with managed services.",
            "GitHub access is read-only. Calendar, Meet, Drive, Calendly, and email are optional integrations.",
            "Role and project checks, organisation isolation, approvals, and an audit log protect the work.",
        ],
        "cards": [
            (64, 205, 254, 270, "BROWSER", "React", "Site + workspace\nPre-boarding portal"),
            (365, 205, 254, 270, "API", "FastAPI", "Auth + roles\nProjects + events"),
            (666, 205, 254, 270, "SUPERVISOR", "LangGraph", "Eight specialists\nHuman approvals"),
            (967, 205, 254, 270, "DATA", "Memory / Postgres", "Code + KT links\nPrograms + audit"),
        ],
        "arrows": [(321, 343, 360, 343), (622, 343, 661, 343), (923, 343, 962, 343)],
    },
    {
        "title": "Explain. Link. Learn. Improve.", "eyebrow": "THE WELCOME WAVE LOOP",
        "narration": [
            "Here is the whole loop. Ravi explains the code once.",
            "Welcome Wave links the recording to source. Maya learns, asks with evidence, and contributes safely.",
            "Priya makes the decisions. The team keeps the knowledge current for the next engineer.",
            "Welcome Wave. Explain once, help every new engineer.",
        ],
        "cards": [
            (70, 215, 215, 245, "EXPLAIN", "Ravi", "Record KT"),
            (315, 215, 215, 245, "LINK", "Welcome Wave", "Code + moment"),
            (560, 215, 215, 245, "LEARN", "Maya", "Explore + ask"),
            (805, 215, 215, 245, "CONTRIBUTE", "Maya + Priya", "Review + decide"),
            (1050, 215, 170, 245, "IMPROVE", "Team", "Keep it fresh"),
        ],
        "arrows": [(287, 341, 311, 341), (532, 341, 556, 341), (777, 341, 801, 341), (1022, 341, 1046, 341)],
    },
]


def font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(MONO if mono else FONT, size)


def text_wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        row = ""
        for word in words:
            candidate = f"{row} {word}".strip()
            if draw.textlength(candidate, font=fnt) > max_width and row:
                lines.append(row)
                row = word
            else:
                row = candidate
        lines.append(row)
    return lines


def draw_label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], label: str, size: int, color: str,
               width: int | None = None, spacing: int = 7, mono: bool = False):
    fnt = font(size, mono)
    lines = text_wrap(draw, label, fnt, width) if width else label.split("\n")
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=fnt, fill=color)
        y += size + spacing


def draw_logo(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float = 1):
    points = [(0, 0), (12, 43), (24, 0), (36, 43), (48, 0)]
    for i in range(4):
        a, b = points[i], points[i + 1]
        draw.line([(x + a[0] * scale, y + a[1] * scale), (x + b[0] * scale, y + b[1] * scale)],
                  fill=TEAL if i == 0 else PAPER, width=max(3, int(7 * scale)))
    draw_label(draw, (x + int(67 * scale), y + int(5 * scale)), "Welcome Wave", int(26 * scale), PAPER)


def make_base(chapter: dict, idx: int) -> Image.Image:
    im = Image.new("RGBA", (WIDTH, HEIGHT), INK)
    d = ImageDraw.Draw(im)
    for x in range(0, WIDTH, 44):
        d.line((x, 0, x, HEIGHT), fill="#111617", width=1)
    for y in range(0, HEIGHT, 44):
        d.line((0, y, WIDTH, y), fill="#111617", width=1)
    draw_logo(d, 69, 27, 0.68)
    draw_label(d, (80, 98), chapter["eyebrow"], 15, TEAL, mono=True)
    draw_label(d, (80, 125), chapter["title"], 38, PAPER)
    draw_label(d, (1125, 42), f"{idx + 1:02d} / {len(CHAPTERS):02d}", 17, MUTED, mono=True)
    for x1, y1, x2, y2 in chapter["arrows"]:
        d.line((x1, y1, x2 - 7, y2), fill=STROKE, width=4)
        d.polygon([(x2, y2), (x2 - 12, y2 - 8), (x2 - 12, y2 + 8)], fill=TEAL)
    d.line((80, 554, 1200, 554), fill=STROKE, width=3)
    return im


def make_card(spec: tuple, index: int) -> Image.Image:
    x, y, w, h, heading, sub, body = spec
    im = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((x, y, x + w, y + h), radius=24, fill=PANEL, outline=STROKE, width=2)
    d.rounded_rectangle((x + 19, y + 18, x + 64, y + 24), radius=3, fill=TEAL)
    draw_label(d, (x + 22, y + 42), heading, 21 if w > 190 else 17, PAPER, width=w - 44)
    draw_label(d, (x + 22, y + 83), sub, 16, TEAL, width=w - 44)
    body_size = 18 if w >= 255 else 16
    draw_label(d, (x + 22, y + 131), body, body_size, MUTED, width=w - 44, spacing=9, mono="/" in body or "()" in body)
    d.ellipse((x + w - 43, y + h - 40, x + w - 23, y + h - 20), outline=TEAL, width=2)
    d.ellipse((x + w - 36, y + h - 33, x + w - 30, y + h - 27), fill=TEAL)
    return im


def caption_layer(sentence: str) -> Image.Image:
    im = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((66, 578, 1214, 672), radius=19, fill=(13, 20, 20, 245), outline=(45, 60, 60, 255), width=2)
    draw_label(d, (92, 596), sentence, 23, PAPER, width=1090, spacing=5)
    return im


def duration(path: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True)
    return float(out.strip())


def make_narration() -> tuple[list[dict], float]:
    WORK.mkdir(parents=True, exist_ok=True)
    tracks = []
    concat = []
    clock = 0.0
    for ci, chapter in enumerate(CHAPTERS):
        start = clock
        captions = []
        for si, sentence in enumerate(chapter["narration"]):
            path = WORK / f"voice-{ci:02d}-{si:02d}.aiff"
            if not path.exists():
                subprocess.run(["say", "-v", "Samantha", "-r", "165", "-o", str(path), sentence], check=True)
            seconds = duration(path)
            captions.append({"start": clock, "end": clock + seconds, "text": sentence})
            concat.append(f"file '{path}'")
            clock += seconds
        tracks.append({"start": start, "end": clock, "captions": captions})
    (WORK / "voice-list.txt").write_text("\n".join(concat) + "\n")
    wav = WORK / "narration.wav"
    if not wav.exists():
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i",
                        str(WORK / "voice-list.txt"), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(wav)], check=True)
    return tracks, duration(wav)


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(tracks: list[dict]):
    lines = []
    n = 0
    for chapter in tracks:
        for cap in chapter["captions"]:
            n += 1
            lines.extend([str(n), f"{srt_time(cap['start'])} --> {srt_time(cap['end'])}", cap["text"], ""])
    (OUT / f"{VIDEO_SLUG}.srt").write_text("\n".join(lines))


def frame(ci: int, progress: float, sentence: str, base: Image.Image, cards: list[Image.Image], captions: dict) -> Image.Image:
    im = base.copy()
    for i, layer in enumerate(cards):
        start = 0.03 + i * (0.28 / max(1, len(cards) - 1))
        alpha = max(0.0, min(1.0, (progress - start) / 0.095))
        if alpha >= 0.999:
            im = Image.alpha_composite(im, layer)
        elif alpha > 0:
            partial = layer.copy()
            partial.putalpha(partial.getchannel("A").point(lambda a: round(a * alpha)))
            im = Image.alpha_composite(im, partial)
    d = ImageDraw.Draw(im)
    # The same small knowledge pulse travels from one decision to the next throughout each scene.
    for j, (x1, y1, x2, y2) in enumerate(CHAPTERS[ci]["arrows"]):
        phase = (progress * 18 + j * 0.29) % 1
        x = x1 + (x2 - x1) * phase
        y = y1 + (y2 - y1) * phase
        d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=TEAL)
    px = int(80 + 1120 * progress)
    d.line((80, 554, px, 554), fill=TEAL, width=4)
    pulse = 5 + int(2 * math.sin(progress * 60))
    d.ellipse((px - pulse, 554 - pulse, px + pulse, 554 + pulse), fill=TEAL)
    im = Image.alpha_composite(im, captions[sentence])
    fade = min(1.0, progress / 0.022, (1 - progress) / 0.018)
    if fade < 1:
        im = Image.blend(Image.new("RGBA", (WIDTH, HEIGHT), INK), im, max(0.0, fade))
    return im.convert("RGB")


def render(tracks: list[dict], seconds: float, preview: bool):
    bases = [make_base(ch, i) for i, ch in enumerate(CHAPTERS)]
    cards = [[make_card(spec, i) for i, spec in enumerate(ch["cards"])] for ch in CHAPTERS]
    cap_images = {s: caption_layer(s) for ch in CHAPTERS for s in ch["narration"]}
    if preview:
        thumbs = []
        for i, ch in enumerate(CHAPTERS):
            f = frame(i, 0.86, ch["narration"][-1], bases[i], cards[i], cap_images)
            thumbs.append(f.resize((640, 360)))
        sheet = Image.new("RGB", (1280, 360 * 5), INK)
        for i, thumb in enumerate(thumbs):
            sheet.paste(thumb, ((i % 2) * 640, (i // 2) * 360))
        sheet.save(WORK / "contact-sheet.jpg", quality=90)
        print(WORK / "contact-sheet.jpg")
        return
    mp4 = OUT / f"{VIDEO_SLUG}.mp4"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pixel_format", "rgb24",
           "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", str(FPS), "-i", "-", "-i", str(WORK / "narration.wav"),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", "-shortest", str(mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        total_frames = math.ceil(seconds * FPS)
        ci = 0
        cap_i = 0
        for fi in range(total_frames):
            t = fi / FPS
            while ci + 1 < len(tracks) and t >= tracks[ci]["end"]:
                ci += 1
                cap_i = 0
            chapter = tracks[ci]
            while cap_i + 1 < len(chapter["captions"]) and t >= chapter["captions"][cap_i]["end"]:
                cap_i += 1
            progress = (t - chapter["start"]) / max(0.01, chapter["end"] - chapter["start"])
            shot = frame(ci, progress, chapter["captions"][cap_i]["text"], bases[ci], cards[ci], cap_images)
            proc.stdin.write(shot.tobytes())
            if fi % (FPS * 30) == 0:
                print(f"Rendered {t:.0f}/{seconds:.0f}s", flush=True)
    finally:
        proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg render failed")
    print(mp4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    tracks, seconds = make_narration()
    write_srt(tracks)
    (WORK / "timing.json").write_text(json.dumps(tracks, indent=2))
    print(f"Narration: {seconds:.1f} seconds", flush=True)
    render(tracks, seconds, args.preview)


if __name__ == "__main__":
    main()
