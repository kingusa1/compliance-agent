"""Benchmark the new agent layer against the current batched analyzer.

Runs both code paths on the same saved transcripts and prints per-file
latency and aggregate time ratio. Used as a go/no-go check before
flipping USE_AGENT_ANALYZER on in production.

Usage:
    ./venv/bin/python3 backend/benchmark_agent_vs_current.py
"""
import asyncio
import time
from pathlib import Path

from app.agent.escalation import run_batch_tiered
from app.agent.tool_handlers import ToolContext
from app.checkpoint_analyzer import _analyze_batch

TRANSCRIPTS = Path(__file__).parent / "transcripts"

SAMPLE_CHECKPOINTS = [
    {"section": i, "name": f"CP{i}", "required": "compliance check",
     "key_phrases": [], "strictness": "mandatory"}
    for i in range(1, 7)
]


def _supplier_from_filename(name: str) -> str:
    n = name.lower()
    if "eon" in n:
        return "E.ON Next"
    if "british" in n:
        return "British Gas"
    if "edf" in n:
        return "EDF"
    if "pozitive" in n:
        return "Pozitive"
    if "scottish" in n:
        return "Scottish Power"
    return "Unknown"


async def run_bench(path: Path):
    transcript = path.read_text(encoding="utf-8")
    supplier = _supplier_from_filename(path.name)
    ctx = ToolContext(
        transcript=transcript, word_data=[], supplier=supplier,
        agent_speaker_label="A", customer_speaker_label="B",
    )

    t0 = time.time()
    _ = await _analyze_batch(transcript, SAMPLE_CHECKPOINTS, supplier, "mandatory")
    current_time = time.time() - t0

    t0 = time.time()
    _ = await run_batch_tiered(ctx, SAMPLE_CHECKPOINTS)
    agent_time = time.time() - t0

    return {
        "file": path.name,
        "supplier": supplier,
        "current_time": current_time,
        "agent_time": agent_time,
    }


async def main():
    if not TRANSCRIPTS.exists():
        print(f"⚠️  no transcripts at {TRANSCRIPTS}")
        return

    files = sorted(TRANSCRIPTS.glob("*.txt"))[:5]
    if not files:
        print("⚠️  no .txt transcripts to benchmark")
        return

    results = []
    print(f"{'File':<72}  {'current':>8}  {'agent':>8}")
    print("-" * 92)
    for txt in files:
        r = await run_bench(txt)
        results.append(r)
        print(f"{r['file'][:72]:<72}  {r['current_time']:>7.1f}s  {r['agent_time']:>7.1f}s")

    total_current = sum(r["current_time"] for r in results)
    total_agent = sum(r["agent_time"] for r in results)
    ratio = total_agent / total_current if total_current else float("inf")

    print("-" * 92)
    print(f"Total: current={total_current:.1f}s  agent={total_agent:.1f}s  (agent/current = {ratio:.2f}x)")
    print()
    if ratio > 3.0:
        print("⚠️  agent is more than 3x slower — investigate prompt size or escalation frequency")
    else:
        print("✅ agent latency within acceptable range")


if __name__ == "__main__":
    asyncio.run(main())
