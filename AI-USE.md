# AI tool and model disclosure

| Tool / model | Used for | Part |
|---|---|---|
| Claude (Opus 5.5) in the Claude app, agent mode | Generated the initial Part 4.1 codebase (backend, frontend, tests, CI) and first drafts of README, CLAUDE.md, design notes, verification notes, evidence log and report from the assignment text | 4.1 |
| Claude (Opus 5.5) in the Claude app (same conversation) | Step-by-step help running the app on macOS (SSL certificate fix, starting backend/frontend), the GitHub token/workflow-scope problem, the submission checklist; filled in the live results in report.md and docs from my recorded database and event log | 4.1 |

## How the choice changed and why

- The agent built and tested the code in its own sandbox, which could not reach Yahoo, so all live testing was done by me on my Mac. When problems came up on my machine (SSL certificates, GitHub token scopes) I used the same chat to diagnose them instead of changing code.

## What I checked myself

- Ran the backend and frontend locally against live Yahoo data, watched the dashboard, turned Wi-Fi off/on, used replay and verify, and recorded the demo video.
- Set up the GitHub repository and pushed the code.
- _Add: which files you read closely (e.g. aggregator.py, store.py, yahoo_live.py) and anything you changed by hand._

All generated code was reviewed and run locally before submission. Disclosed
AI use follows the course policy.
