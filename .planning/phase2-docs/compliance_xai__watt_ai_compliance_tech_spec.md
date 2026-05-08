# Watt_AI_Compliance_Tech_Spec

Watt Utilities – AI Compliance System (Full Technical Specification)

## 1. System Overview

This document defines the full architecture, logic, and data structure required to build the Watt Utilities AI Compliance Transcribe & Flagging System.

The system will:
- Transcribe calls in real time
- Analyse conversations using rule-based + NLP logic
- Flag compliance risks
- Score calls
- Generate automated feedback
- Trigger actions (block / review / pass)


## 2. System Architecture

Components:
1. Call Audio Input Layer
2. Speech-to-Text Engine
3. NLP Processing Engine
4. Compliance Rules Engine
5. Scoring Engine
6. Alert Engine (Real-Time)
7. Output Engine (Reports + Emails)
8. Database Storage


## 3. Call Segmentation Logic

AI must segment calls into:
- Introduction
- Qualification
- Pitch
- Transfer / Passover
- Verbal Contract
- Close

Each segment runs different rule sets.


## 4. Compliance Rules Engine (Sample JSON)


{
  "rule_id": "IDENTITY_CHECK",
  "stage": "INTRO",
  "condition": "no mention of Watt Utilities within 20 seconds",
  "severity": "CRITICAL",
  "action": "BLOCK_DEAL"
}

{
  "rule_id": "MISLEADING_PRICING",
  "stage": "PITCH",
  "condition": "detect phrases like 'guaranteed savings'",
  "severity": "CRITICAL",
  "action": "BLOCK_DEAL"
}


## 5. Database Schema


Table: Calls
- call_id
- agent_name
- date
- duration
- transcript

Table: Compliance_Results
- call_id
- score
- status
- flags
- risk_tags

Table: Flags
- flag_id
- rule_id
- severity
- timestamp
- transcript_snippet

Table: Agents
- agent_id
- name
- team
- compliance_score_avg


## 6. Scoring Engine Logic


Start score = 100

CRITICAL = -30
HIGH = -15
MEDIUM = -5

IF score < 50 → FAIL
IF score 50–69 → REVIEW
IF score 70–89 → COACHING
IF score 90+ → PASS


## 7. Real-Time Alert Engine


Triggers:
- Missing intro → “State Watt Utilities clearly”
- Risk phrase → “Avoid guaranteed savings”
- No decision maker → “Confirm authority”
- Transfer without consent → “Get consent first”

Alerts delivered via:
- Agent screen popup
- Whisper audio (optional)


## 8. Output Engine


Outputs per call:
1. Compliance Score
2. Flag List
3. Risk Tags
4. Action (Block / Review / Pass)
5. Auto-generated feedback email


## 9. Risk Tagging System


Tags:
- Ombudsman Risk
- Mis-selling Risk
- Complaint Risk
- Cancellation Risk


## 10. Automation Triggers


IF critical flag → Block deal
IF 3+ critical in a week → escalate agent
IF repeated issues → assign retraining


## 11. Final Enforcement Rule


ANY CRITICAL BREACH = AUTOMATIC DEAL BLOCK
