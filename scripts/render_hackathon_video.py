#!/usr/bin/env python3
"""Render the short, narrated Welcome-Kit → Welcome Wave hackathon walkthrough."""

import render_explainer as movie

movie.VIDEO_SLUG = "welcome-wave-welcome-kit-demo"
movie.WORK = movie.ROOT / "dist" / "welcome-kit-demo-work"
movie.FPS = 10
movie.CHAPTERS = [
    {
        "eyebrow": "THE TWO APPLICATIONS", "title": "A welcome experience with a brain",
        "narration": [
            "Welcome Kit is the interactive sample application. Welcome Wave is the onboarding product that learns its real codebase.",
            "A new engineer explores Welcome Kit's missions, then asks Welcome Wave how those missions actually work.",
            "The repo and the live site are real. Mission progress stays in the engineer's browser.",
        ],
        "cards": [
            (65, 202, 350, 275, "WELCOME KIT", "The sample product", "3D orbit and missions\nPublic GitHub repository\nBrowser-local progress"),
            (465, 202, 350, 275, "WELCOME WAVE", "The onboarding product", "Indexes real source\nPlans the new hire's path\nAnswers with citations"),
            (865, 202, 350, 275, "THE NEW HIRE", "Connects the two", "Explores a mission\nAsks how it works\nFinds the source file"),
        ],
        "arrows": [(418, 339, 462, 339), (818, 339, 862, 339)],
    },
    {
        "eyebrow": "01  CONNECT REAL CODE", "title": "Turn a repository into evidence",
        "narration": [
            "In Welcome Wave, open the Welcome Kit project, then Settings and Repository. Connect Vijayabaskar R dash zero six slash welcome kit.",
            "Wait for Ready. The Librarian indexes files such as source slash lib slash mission dot t s without changing the repository.",
            "A code citation is a link to inspect. It is not a made-up answer or a write back to GitHub.",
        ],
        "cards": [
            (65, 202, 350, 275, "SETTINGS", "Repository", "VijayabaskarR-06/\nwelcome-kit\nStatus: Ready"),
            (465, 202, 350, 275, "LIBRARIAN", "Read-only index", "src/App.tsx\nsrc/lib/mission.ts\nSearchable chunks"),
            (865, 202, 350, 275, "EVIDENCE", "File and line", "Answer cites code\nOpen Code Tour\nVerify the source"),
        ],
        "arrows": [(418, 339, 462, 339), (818, 339, 862, 339)],
    },
    {
        "eyebrow": "02  ENROL A PERSON", "title": "The plan waits for a manager",
        "narration": [
            "From Team and Enrol, choose a hire, mentor, start date, role, and the Welcome Kit project.",
            "The Planner drafts a program. A manager opens Approvals, reviews the tasks, and assigns the approved plan.",
            "The new hire signs in and sees a real onboarding board with next steps and an Ask the Project entry point.",
        ],
        "cards": [
            (65, 202, 350, 275, "TEAM → ENROL", "Manager sets context", "Hire and mentor\nWelcome-Kit project\nRole and start date"),
            (465, 202, 350, 275, "PLANNER", "Draft only", "Milestones and tasks\nBased on project context\nAwaiting review"),
            (865, 202, 350, 275, "APPROVALS", "Human decision", "Inspect the proposal\nAssign the plan\nNew hire sees it"),
        ],
        "arrows": [(418, 339, 462, 339), (818, 339, 862, 339)],
    },
    {
        "eyebrow": "03  ASK WITH A CITATION", "title": "From mission to implementation",
        "narration": [
            "The hire asks: Where is mission progress stored, and what happens when I complete a mission?",
            "Welcome Wave searches the indexed Welcome Kit code and cites the file behind the answer.",
            "Open the cited source in Code Tour. Show the path through source slash App dot t s x and source slash lib slash mission dot t s.",
        ],
        "cards": [
            (65, 202, 350, 275, "QUESTION", "New hire", "Where is mission\nprogress stored?\nWhat marks completion?"),
            (465, 202, 350, 275, "GUIDE", "Grounded answer", "Searches the index\nReturns a code citation\nCan say when unsure"),
            (865, 202, 350, 275, "CODE TOUR", "Inspect it yourself", "src/App.tsx\nsrc/lib/mission.ts\nFollow the click path"),
        ],
        "arrows": [(418, 339, 462, 339), (818, 339, 862, 339)],
    },
    {
        "eyebrow": "04  VOICE & CONTROL", "title": "Conversation can propose work",
        "narration": [
            "With an active onboarding, the hire can open Voice and allow the microphone. Agora carries the room and Murf is configured for assistant speech.",
            "The assistant can look up project context and propose a task or a knowledge transfer request. A manager must approve a write.",
            "For the hackathon, rehearse the live call before recording. Show only transcript, citations, and action records that the session actually produces.",
        ],
        "cards": [
            (65, 202, 350, 275, "VOICE", "Hire starts a call", "Active onboarding\nMicrophone permission\nAgora room"),
            (465, 202, 350, 275, "MURF + TOOLS", "Spoken guidance", "Project lookup\nCited context\nProposed next step"),
            (865, 202, 350, 275, "APPROVAL", "Manager stays in charge", "Review task or KT request\nApprove the write\nShow real record status"),
        ],
        "arrows": [(418, 339, 462, 339), (818, 339, 862, 339)],
    },
]

if __name__ == "__main__":
    movie.main()
