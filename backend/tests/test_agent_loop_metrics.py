from app.observability_metrics import LLM_CALLS_TOTAL


def _counter_value(model: str, escalated: str) -> float:
    for sample in LLM_CALLS_TOTAL.collect()[0].samples:
        if sample.name.endswith("_total") and sample.labels == {"model": model, "escalated": escalated}:
            return sample.value
    return 0.0


def test_record_llm_call_helper_emits_counter():
    """Smoke: confirms the import path the agent loop will use."""
    from app.observability_metrics import record_llm_call
    before = _counter_value("gemini-2.5-flash", "false")
    record_llm_call("gemini-2.5-flash", duration_seconds=0.3, escalated=False)
    after = _counter_value("gemini-2.5-flash", "false")
    assert after - before == 1
