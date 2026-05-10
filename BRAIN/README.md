# 🧠 Compliance Agent — Project Brain

A self-contained Obsidian vault documenting **everything** about this project — architecture, domain, AI pipeline, decisions, current live state, deploy commands, credentials, demo scripts.

## How to use

### As a human
1. Open this folder (`BRAIN/`) in **Obsidian** as a vault
2. Start at [`00_INDEX.md`](./00_INDEX.md) — it links everywhere
3. Every note is wikilinked (`[[…]]`) so the graph view shows the structure

### As Claude (next session)
When the user says **"read brain"** / **"read obsidian"**:
1. Read `BRAIN/00_INDEX.md`
2. Read `BRAIN/05_State/Live_State.md`
3. Read the most recent file in `BRAIN/04_Sessions/`
4. Don't re-discover state from scratch — cite file paths when answering

When work happens that changes anything:
1. Append to today's `BRAIN/04_Sessions/<date>_Session.md`
2. If state changed → update `BRAIN/05_State/Live_State.md`
3. If a decision was made → append to `BRAIN/04_Sessions/Decisions.md`
4. Commit the BRAIN/ folder so future sessions see the update

## Directory layout

```
BRAIN/
├── 00_INDEX.md                      ← entry point, wikilinks everywhere
├── README.md                        ← this file
├── 01_Project/
│   ├── Overview.md
│   ├── Architecture.md
│   ├── Stack.md
│   └── Deploy.md
├── 02_Domain/
│   ├── Watt_Compliance.md           ← 8 standards, 27 rejection codes
│   ├── Suppliers.md                 ← 6 canonical suppliers + alias map
│   ├── Scripts.md                   ← 15 scripts + auto-match rules
│   └── Lifecycle.md                 ← E.ON 2-stage vs others 3-stage
├── 03_AI_Pipeline/
│   ├── Pipeline_Stages.md           ← 6 steps per upload
│   ├── Speaker_Detection.md         ← signal-based agent vs customer
│   ├── Quality_Agent.md             ← cross-call identity resolver
│   └── Future_Agents.md             ← multi-agent roadmap
├── 04_Sessions/
│   ├── 2026-05-10_Session.md        ← today's full work log
│   └── Decisions.md                 ← architectural decisions (newest first)
├── 05_State/
│   ├── Live_State.md                ← what's deployed RIGHT NOW
│   ├── Test_Calls.md                ← test data + verdicts
│   └── Known_Issues.md              ← open gaps + workarounds
├── 06_Operations/
│   ├── Deploy_Commands.md           ← copy/paste cheat sheet
│   ├── Routes_Map.md                ← every URL on FE + BE
│   └── Credentials.md               ← where each secret lives (NOT the secrets)
└── 07_Tomorrow/
    ├── Project_Handover.md          ← demo script for the handover
    └── Next_Steps.md                ← post-handover roadmap
```
