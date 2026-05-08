from prometheus_client import REGISTRY
from app.observability_metrics import (
    record_pipeline_step,
    record_llm_call,
    PIPELINE_STEP_DURATION,
    LLM_CALLS_TOTAL,
    LLM_CALL_DURATION,
)


def test_pipeline_step_metric_observes_duration():
    record_pipeline_step("transcribe", 1.234)
    samples = list(PIPELINE_STEP_DURATION.collect()[0].samples)
    count = next(s for s in samples if s.name.endswith("_count") and s.labels.get("step") == "transcribe")
    assert count.value >= 1


def test_llm_calls_counter_increments():
    before = _counter_value(LLM_CALLS_TOTAL, model="gemini-flash", escalated="false")
    record_llm_call("gemini-flash", duration_seconds=0.5, escalated=False)
    after = _counter_value(LLM_CALLS_TOTAL, model="gemini-flash", escalated="false")
    assert after - before == 1


def test_llm_escalation_labelled_separately():
    record_llm_call("claude-sonnet", duration_seconds=2.0, escalated=True)
    val = _counter_value(LLM_CALLS_TOTAL, model="claude-sonnet", escalated="true")
    assert val >= 1


def _counter_value(metric, **labels) -> float:
    for sample in metric.collect()[0].samples:
        if sample.name.endswith("_total") and sample.labels == labels:
            return sample.value
    return 0.0
