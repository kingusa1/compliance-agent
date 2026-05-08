from app.schemas import CheckpointResult


def test_checkpoint_result_accepts_start_and_end_ms():
    cp = CheckpointResult(
        section=1,
        name="VAT disclosure",
        status="pass",
        evidence="the prices include VAT at the prevailing rate",
        notes="Agent read it verbatim.",
        start_ms=42_000,
        end_ms=47_500,
    )
    assert cp.start_ms == 42_000
    assert cp.end_ms == 47_500


def test_checkpoint_result_defaults_start_and_end_to_none():
    cp = CheckpointResult(
        section=2,
        name="Credit-check consent",
        status="fail",
        evidence="",
        notes="Agent never raised credit-check consent.",
    )
    assert cp.start_ms is None
    assert cp.end_ms is None
