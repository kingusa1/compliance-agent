"""Integration: simulating a step run records duration in the histogram."""
import time
from prometheus_client import REGISTRY
from app.observability_metrics import (
    PIPELINE_STEP_DURATION,
    record_pipeline_step,
)


def _bucket_count(step_name: str) -> float:
    for sample in PIPELINE_STEP_DURATION.collect()[0].samples:
        if sample.name.endswith("_count") and sample.labels.get("step") == step_name:
            return sample.value
    return 0.0


def test_record_pipeline_step_increments_count():
    before = _bucket_count("transcribe")
    record_pipeline_step("transcribe", 0.12)
    after = _bucket_count("transcribe")
    assert after - before == 1
