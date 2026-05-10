---
created: 2026-05-10
updated: 2026-05-10
tags: [ai, speakers, detection]
---

# Speaker detection — signal-based agent vs customer

## Problem
Deepgram returns diarised speakers as `speaker: 0` and `speaker: 1`. The OLD code labelled `speaker == 0 → Agent` always. This is wrong on inbound calls where the customer answers first ("hello?" = speaker 0 = agent? NO, that's the customer).

## Fix shipped 2026-05-10
`backend/app/transcription.py:_detect_agent_speaker(words)`:
1. Build a per-speaker word bag (lowercase tokens).
2. Score each speaker by how often broker-side phrases appear:
   - Self-introductions: "my name is", "calling from", "third party"
   - Energy domain: "your electricity supply", "your current contract", "renewal", "best price", "cheapest price", "tariff"
   - Sales: "I'll transfer", "pricing manager", "decision maker", "letter of authority", "loa"
   - Suppliers: "british gas", "scottish power", "edf", "eon", "e.on", "pozitive", "bgl"
3. Speaker with the higher broker-signal score = Agent.
4. Tiebreak: speaker who talks more (brokers carry the call ~3:1 in Watt's corpus).

## Where it runs
`format_diarized_transcript(words)` calls `_detect_agent_speaker(words)` ONCE before iterating, then uses that speaker_id consistently across the transcript output. So the transcript text is labelled `[MM:SS] Agent: …` / `[MM:SS] Customer: …` correctly.

## Re-transcription required for old calls
The transcript text is **cached** on `Call.transcript` after Step 2. On `/retry`, the pipeline DOESN'T re-transcribe — it reuses cached text. So OLD calls still have the old (sometimes-wrong) labels until a fresh upload OR until `transcript` is manually cleared.

## Verified live
Crosby Garage call — labels correct after fresh pipeline run:
```
[00:01] Customer: [phone_number_1]
[00:03] Agent: hello
[00:05] Customer: hello
[00:06] Agent: hi good morning is is it possible to speak to jay …
[00:10] Customer: yes speaking
[00:11] Agent: hi jay my name is paris …
```

See [[03_AI_Pipeline/Pipeline_Stages]] for the full pipeline.
See [[03_AI_Pipeline/Quality_Agent]] for cross-call resolution that uses these labels.
