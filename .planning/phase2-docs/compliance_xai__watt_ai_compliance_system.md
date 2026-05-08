# Watt_AI_Compliance_System

Watt Utilities – AI Compliance Transcribe & Flagging System

## 1. Objective

Build an AI system that transcribes, analyses, and flags compliance risks across:
- Lead Generation Calls (Pre-Sales)
- Verbal Contract Calls (Closing)

The system must detect:
- Language risks
- Behaviour risks
- Missing compliance steps
- Script adherence failures

## 2. Core AI Structure

The AI must:
1. Transcribe the call
2. Segment the call into stages
3. Run detection rules
4. Output flags, score, and actions

## 3. Lead Generation Compliance Rules

CRITICAL FLAGS:
- No mention of “Watt Utilities” in first 20 seconds → BLOCK DEAL
- Supplier impersonation → BLOCK DEAL
- Guaranteed savings / “best price” language → BLOCK DEAL
- Pressure selling after objection → BLOCK DEAL

HIGH FLAGS:
- No decision maker confirmation
- No contract/renewal check
- Confusing explanations

MEDIUM FLAGS:
- Weak introduction
- Poor structure

## 4. Verbal Contract Compliance Rules

CRITICAL FLAGS:
- Script not followed
- Commission not disclosed
- Contract terms missing (supplier, term, rates)
- No clear customer agreement
- Agent answers for customer

HIGH FLAGS:
- Script rushed
- No summary
- Customer confusion

## 5. Language Detection Engine

Flag phrases such as:
- “Guaranteed savings”
- “Best price”
- “Whole market”
- “You need to do this now”

Detect intent, not just exact wording.

## 6. Output Requirements

Each call must generate:
- Compliance Score (0–100)
- Flag List
- Risk Tags (Complaint / Ombudsman Risk)
- Action (Pass / Review / Block)

## 7. Real-Time Alerts

AI should prompt agents live:
- “State Watt Utilities clearly”
- “Confirm decision maker”
- “Avoid guaranteed savings”
- “Get consent before transfer”

## 8. Scoring Logic

Start at 100:
- Critical = -30
- High = -15
- Medium = -5

<50 = Fail
50–69 = Review
70–89 = Coaching
90+ = Pass

## 9. Final Note

System must enforce:
ANY critical breach = automatic deal block.
No exceptions.