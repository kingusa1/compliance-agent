"""Prometheus metric registry for the Compliance pipeline.

Three primary metrics:
  - pipeline_step_duration_seconds (Histogram, labelled by `step`)
  - llm_calls_total                 (Counter,   labelled by `model`, `escalated`)
  - llm_call_duration_seconds       (Histogram, labelled by `model`)

These feed the Pipeline + LLM Grafana dashboards. API/HTTP metrics are
provided by prometheus-fastapi-instrumentator and live under the
`http_request_*` namespace — no work needed here for those.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram


PIPELINE_STEP_DURATION = Histogram(
    "pipeline_step_duration_seconds",
    "Time spent in each compliance pipeline step",
    labelnames=("step",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

LLM_CALLS_TOTAL = Counter(
    "llm_calls_total",
    "Number of LLM calls issued by the pipeline",
    labelnames=("model", "escalated"),
)

LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "Latency of individual LLM calls",
    labelnames=("model",),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 60.0),
)


def record_pipeline_step(step: str, duration_seconds: float) -> None:
    PIPELINE_STEP_DURATION.labels(step=step).observe(duration_seconds)


def record_llm_call(model: str, duration_seconds: float, escalated: bool = False) -> None:
    LLM_CALLS_TOTAL.labels(model=model, escalated=str(escalated).lower()).inc()
    LLM_CALL_DURATION.labels(model=model).observe(duration_seconds)
