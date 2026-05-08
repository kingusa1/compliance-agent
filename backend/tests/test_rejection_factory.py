import pytest
from unittest.mock import AsyncMock, patch

from app.rejection_factory import build_rejection_for_call, should_create_rejection


def test_should_create_rejection_returns_true_when_below_threshold():
    assert should_create_rejection(score=10, total=24) is True


def test_should_create_rejection_returns_false_at_or_above_threshold():
    assert should_create_rejection(score=17, total=24) is False
    assert should_create_rejection(score=24, total=24) is False


def test_should_create_rejection_handles_zero_total():
    assert should_create_rejection(score=0, total=0) is False


@pytest.mark.asyncio
async def test_build_rejection_for_call_classifies_category_and_writes_reason():
    failing_cps = [
        {"name": "Pricing Disclosure", "status": "fail", "evidence": "agent stated INCLUDE VAT", "notes": "incorrect"},
        {"name": "Terms Availability", "status": "partial", "evidence": "", "notes": ""},
    ]
    with patch("app.rejection_factory._classify_category", new_callable=AsyncMock) as cls, \
         patch("app.rejection_factory._summarise_reason", new_callable=AsyncMock) as rsn, \
         patch("app.rejection_factory._propose_fix", new_callable=AsyncMock) as fix, \
         patch("app.rejection_factory._propose_narrative", new_callable=AsyncMock) as nar:
        cls.return_value = "COMPLIANCE_ISSUE"
        rsn.return_value = "Agent stated prices include VAT, CCL and Green deal."
        fix.return_value = "AMENDMENT_CALL"  # remediation_action enum value
        nar.return_value = "Inform customer of correct rates; bill will increase."
        result = await build_rejection_for_call(
            call_id="abc",
            customer_slug="evangelical-church",
            supplier="E.ON Next",
            sales_agent="Afaq",
            failing_checkpoints=failing_cps,
        )
    assert result["category"] == "COMPLIANCE_ISSUE"
    assert "include VAT" in result["rejection_reason"]
    assert result["fix_required"] == "AMENDMENT_CALL"
    # The corrective-action narrative now lands on fix_narrative; outcome_narrative
    # stays nullable for terminal-state close-out text the reviewer enters later.
    assert "Inform customer" in result["fix_narrative"]
    assert "outcome_narrative" not in result


@pytest.mark.asyncio
async def test_build_rejection_skips_fix_required_when_llm_returns_invalid_enum():
    """LLM picks an enum that doesn't match REMEDIATION_ACTIONS → fix_required
    omitted from payload (column is nullable). Prevents IntegrityError on insert."""
    failing_cps = [{"name": "X", "status": "fail", "evidence": "", "notes": ""}]
    with patch("app.rejection_factory._classify_category", new_callable=AsyncMock) as cls, \
         patch("app.rejection_factory._summarise_reason", new_callable=AsyncMock) as rsn, \
         patch("app.rejection_factory._propose_fix", new_callable=AsyncMock) as fix, \
         patch("app.rejection_factory._propose_narrative", new_callable=AsyncMock) as nar:
        cls.return_value = "PROCESS_FAILURE"
        rsn.return_value = "..."
        fix.return_value = None  # invalid enum normalised to None by _propose_fix
        nar.return_value = ""
        result = await build_rejection_for_call(
            call_id="x", customer_slug=None, supplier=None, sales_agent=None,
            failing_checkpoints=failing_cps,
        )
    assert "fix_required" not in result
    assert "fix_narrative" not in result
    assert "outcome_narrative" not in result
