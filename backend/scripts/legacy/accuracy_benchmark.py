"""
Benchmark current production accuracy against consensus ground truth.

Compares what our production system scored (in SQLite) vs consensus GT files.

Usage:
    python3 accuracy_benchmark.py
    python3 accuracy_benchmark.py --verbose
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime


def load_ground_truth(short_id: str) -> dict | None:
    """Load consensus GT for a call."""
    path = f"benchmark/ground_truth/{short_id}_consensus.json"
    if not os.path.exists(path):
        return None
    return json.load(open(path))


def compare(production_results: list[dict], gt_consensus: list[dict]) -> dict:
    """Compare production results against ground truth consensus."""
    gt_map = {}
    for c in gt_consensus:
        gt_map[c["name"].lower().strip()] = c

    matches = 0
    mismatches = 0
    false_fails = []
    false_passes = []
    partial_disagreements = []
    missing = 0
    details = []

    for pr in production_results:
        pr_name = pr.get("name", "").lower().strip()
        gt = gt_map.get(pr_name)

        # Try fuzzy name match
        if not gt:
            for gn, gc in gt_map.items():
                if gn in pr_name or pr_name in gn:
                    gt = gc
                    break

        if not gt:
            missing += 1
            continue

        pr_status = pr.get("status", "unknown")
        gt_status = gt["status"]
        gt_confidence = gt.get("pct", 0)

        match = pr_status == gt_status
        if match:
            matches += 1
        else:
            mismatches += 1

            error_type = "unknown"
            if gt_status == "pass" and pr_status in ("fail", "unverified"):
                error_type = "false_fail"
                false_fails.append(pr.get("name", ""))
            elif gt_status in ("fail",) and pr_status == "pass":
                error_type = "false_pass"
                false_passes.append(pr.get("name", ""))
            elif "partial" in (gt_status, pr_status):
                error_type = "partial_disagreement"
                partial_disagreements.append(pr.get("name", ""))

            details.append({
                "checkpoint": pr.get("name", ""),
                "production": pr_status,
                "ground_truth": gt_status,
                "gt_confidence": gt_confidence,
                "error_type": error_type,
                "production_evidence": pr.get("evidence", "")[:100],
                "gt_evidence": gt.get("evidence", "")[:100],
            })

    total = matches + mismatches
    accuracy = matches / total * 100 if total else 0

    return {
        "accuracy": round(accuracy, 1),
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "total": total,
        "false_fails": len(false_fails),
        "false_passes": len(false_passes),
        "partial_disagreements": len(partial_disagreements),
        "false_fail_names": false_fails,
        "false_pass_names": false_passes,
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    db = sqlite3.connect("compliance.db")
    db.row_factory = sqlite3.Row

    calls = db.execute(
        "SELECT * FROM calls WHERE status='completed' AND checkpoint_results IS NOT NULL AND script_id IS NOT NULL"
    ).fetchall()

    os.makedirs("benchmark", exist_ok=True)

    print(f"\n{'='*70}")
    print(f"ACCURACY BENCHMARK — Production vs Consensus Ground Truth")
    print(f"{'='*70}\n")

    all_results = []
    total_matches = 0
    total_mismatches = 0
    total_checkpoints = 0
    error_counts = defaultdict(int)
    checkpoint_errors = defaultdict(list)
    supplier_stats = defaultdict(lambda: {"matches": 0, "mismatches": 0, "total": 0})

    for call in calls:
        short_id = call["id"][:8]
        gt = load_ground_truth(short_id)
        if not gt:
            continue

        production_results = json.loads(call["checkpoint_results"])
        gt_consensus = gt["consensus"]
        supplier = call["detected_supplier"] or "Unknown"

        result = compare(production_results, gt_consensus)

        all_results.append({
            "call_id": short_id,
            "supplier": supplier,
            "accuracy": result["accuracy"],
            "matches": result["matches"],
            "mismatches": result["mismatches"],
            "total": result["total"],
            "false_fails": result["false_fails"],
            "false_passes": result["false_passes"],
            "partial_disagreements": result["partial_disagreements"],
            "production_score": call["score"],
            "gt_score": gt["stats"]["score"],
        })

        total_matches += result["matches"]
        total_mismatches += result["mismatches"]
        total_checkpoints += result["total"]

        supplier_stats[supplier]["matches"] += result["matches"]
        supplier_stats[supplier]["mismatches"] += result["mismatches"]
        supplier_stats[supplier]["total"] += result["total"]

        for d in result["details"]:
            error_counts[d["error_type"]] += 1
            checkpoint_errors[d["checkpoint"]].append({
                "call_id": short_id,
                "type": d["error_type"],
                "production": d["production"],
                "gt": d["ground_truth"],
            })

        status = "OK" if result["accuracy"] >= 90 else "LOW" if result["accuracy"] >= 70 else "BAD"
        print(f"  {short_id} | {supplier:15s} | Prod: {call['score']:>7s} | GT: {gt['stats']['score']:>7s} | Acc: {result['accuracy']:>5.1f}% | FF:{result['false_fails']} FP:{result['false_passes']} PD:{result['partial_disagreements']} [{status}]")

        if args.verbose and result["details"]:
            for d in result["details"]:
                print(f"    {d['error_type']:22s} | {d['checkpoint'][:40]:40s} | prod={d['production']:8s} gt={d['ground_truth']:8s} ({d['gt_confidence']}%)")

    # Overall stats
    overall_accuracy = total_matches / total_checkpoints * 100 if total_checkpoints else 0

    print(f"\n{'='*70}")
    print(f"OVERALL RESULTS")
    print(f"{'='*70}")
    print(f"Calls benchmarked: {len(all_results)}")
    print(f"Total checkpoints: {total_checkpoints}")
    print(f"Matches: {total_matches} | Mismatches: {total_mismatches}")
    print(f"OVERALL ACCURACY: {overall_accuracy:.1f}%")
    print()

    # Error breakdown
    print(f"Error Breakdown:")
    for etype, count in sorted(error_counts.items(), key=lambda x: -x[1]):
        pct = count / total_mismatches * 100 if total_mismatches else 0
        print(f"  {etype:25s}: {count:>4d} ({pct:.0f}% of errors)")

    # Per-supplier
    print(f"\nPer-Supplier Accuracy:")
    for supplier, stats in sorted(supplier_stats.items()):
        acc = stats["matches"] / stats["total"] * 100 if stats["total"] else 0
        print(f"  {supplier:20s}: {acc:.1f}% ({stats['matches']}/{stats['total']})")

    # Most-errored checkpoints
    print(f"\nTop 10 Most-Errored Checkpoints:")
    for cp_name, errors in sorted(checkpoint_errors.items(), key=lambda x: -len(x[1]))[:10]:
        error_types = [e["type"] for e in errors]
        type_summary = ", ".join(f"{t}:{error_types.count(t)}" for t in set(error_types))
        print(f"  {cp_name[:45]:45s} | {len(errors)} errors | {type_summary}")

    # Save CSV
    csv_path = "benchmark/accuracy_report.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "call_id", "supplier", "accuracy", "matches", "mismatches", "total",
            "false_fails", "false_passes", "partial_disagreements",
            "production_score", "gt_score",
        ])
        writer.writeheader()
        writer.writerows(all_results)

    # Save summary JSON
    summary = {
        "timestamp": datetime.now().isoformat(),
        "calls_benchmarked": len(all_results),
        "total_checkpoints": total_checkpoints,
        "overall_accuracy": round(overall_accuracy, 1),
        "total_matches": total_matches,
        "total_mismatches": total_mismatches,
        "error_breakdown": dict(error_counts),
        "per_supplier": {s: {"accuracy": round(d["matches"]/d["total"]*100, 1) if d["total"] else 0, **d} for s, d in supplier_stats.items()},
        "top_errored_checkpoints": [
            {"name": cp, "error_count": len(errs), "errors": errs}
            for cp, errs in sorted(checkpoint_errors.items(), key=lambda x: -len(x[1]))[:15]
        ],
    }
    with open("benchmark/accuracy_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nSaved: {csv_path}")
    print(f"Saved: benchmark/accuracy_summary.json")

    db.close()


if __name__ == "__main__":
    main()
