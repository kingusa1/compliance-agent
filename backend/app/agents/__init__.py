"""Tracker-autofill specialist agents.

Three agents that close the gap between what the pipeline auto-extracts
today and what the tracker XLSX expects every column to be:

- ``date_extractor.DateExtractorAgent`` — pulls Expected Live Date from
  the transcript (Haiku 4.5; cheap regex pre-pass).
- ``rejection_advisor.RejectionAdvisorAgent`` — fills Category +
  Fix Required on every non-compliant rejection (Opus 4.7).
- ``deadline_computer.DeadlineComputerAgent`` — assigns a sensible
  deadline based on rejection severity + expected_live_date
  (no LLM, pure compute).

See [[BRAIN/03_AI_Pipeline/Tracker_Autofill_Plan]] for the per-column
source map and rollout plan.
"""
from __future__ import annotations

from app.agents.date_extractor import (
    DateExtractorAgent,
    extract_dates_for_call,
)
from app.agents.rejection_advisor import (
    RejectionAdvisorAgent,
    advise_rejection,
)
from app.agents.deadline_computer import (
    DeadlineComputerAgent,
    compute_deadline,
)

__all__ = [
    "DateExtractorAgent",
    "extract_dates_for_call",
    "RejectionAdvisorAgent",
    "advise_rejection",
    "DeadlineComputerAgent",
    "compute_deadline",
]
