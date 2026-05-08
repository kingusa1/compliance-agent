"""Reprocess all existing calls through the new script variant detection pipeline.
Uses existing transcripts — skips Deepgram. Only re-runs detection + analysis."""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.analysis import detect_supplier, detect_script_variant
from app.checkpoint_analyzer import analyze_all_checkpoints
from app.database import SessionLocal
from app.logger import log
from app.models import Call, CallCheckpoint, Script


async def reprocess_call(call: Call, db) -> dict:
    """Reprocess a single call using existing transcript."""
    call_id = call.id
    transcript = call.transcript
    original_name = call.filename

    log.info(f"{'='*60}")
    log.info(f"REPROCESS start call_id={call_id} file=\"{original_name}\"")

    if not transcript:
        log.warning(f"REPROCESS skip call_id={call_id} — no transcript")
        return {"status": "skipped", "reason": "no transcript"}

    # Step 1: Detect supplier
    t0 = time.time()
    detected = await detect_supplier(transcript)
    log.info(f"DETECT → \"{detected}\" ({time.time()-t0:.1f}s)")
    call.detected_supplier = detected

    # Step 2: Find matching scripts
    matching_scripts = db.query(Script).filter(
        Script.supplier_name.ilike(f"%{detected}%"),
        Script.active == True,
    ).all()

    if not matching_scripts:
        log.warning(f"REPROCESS no scripts for \"{detected}\" — skipping analysis")
        return {"status": "no_script", "supplier": detected}

    # Step 3: Pick the right variant
    script = None
    if len(matching_scripts) == 1:
        script = matching_scripts[0]
        log.info(f"SCRIPT single match → \"{script.script_name}\"")
    else:
        log.info(f"SCRIPT {len(matching_scripts)} variants for \"{detected}\", detecting...")
        t1 = time.time()
        script_options = [
            {"index": i, "id": s.id, "script_name": s.script_name}
            for i, s in enumerate(matching_scripts)
        ]
        best_idx = await detect_script_variant(transcript, detected, script_options)
        script = matching_scripts[best_idx]
        log.info(f"SCRIPT VARIANT → \"{script.script_name}\" ({time.time()-t1:.1f}s)")

    # Step 4: Rename file
    call.script_id = script.id
    call.detected_supplier = script.supplier_name
    safe_supplier = detected.replace(" ", "_").replace(".", "")
    safe_script = script.script_name.replace(" ", "_")

    # Strip any previous prefix before re-renaming
    base_name = original_name
    if "__" in base_name:
        parts = base_name.split("__")
        base_name = parts[-1]  # get the original name after last __

    ext = os.path.splitext(base_name)[1]
    base = os.path.splitext(base_name)[0]
    new_name = f"{safe_supplier}__{safe_script}__{base}{ext}"
    call.filename = new_name
    log.info(f"RENAME → \"{new_name}\"")

    # Step 5: Re-analyze
    checkpoints = json.loads(script.checkpoints)
    log.info(f"ANALYZE {len(checkpoints)} checkpoints...")
    t2 = time.time()

    result = await analyze_all_checkpoints(transcript, checkpoints, script.mode)

    call.agent_name = result["agent_name"]
    call.customer_name = result["customer_name"]

    verified_results = result["results"]
    call.checkpoint_results = json.dumps(verified_results)

    # Clear old checkpoints and save new ones
    db.query(CallCheckpoint).filter_by(call_id=call_id).delete()
    for cp in verified_results:
        db.add(CallCheckpoint(
            call_id=call_id,
            rule_text=cp["name"],
            passed=cp["status"] == "pass",
            excerpt=cp.get("evidence"),
        ))

    # Score
    summary = result["summary"]
    total_checkpoints = len(verified_results)
    error_count = summary["error"]

    if error_count > total_checkpoints / 2:
        call.score = summary["score"]
        call.compliant = False
        call.status = "needs_manual_review"
        call.reason = f"{error_count} of {total_checkpoints} checkpoints failed to analyze."
    else:
        call.score = summary["score"]
        call.compliant = summary["compliant"]
        passed = summary["passed"]
        failed = summary["failed"]
        partial = summary["partial"]
        call.reason = f"Score: {call.score}. " + (
            "All checkpoints passed." if call.compliant
            else f"{failed} checkpoint(s) missed, {partial} partial."
        )
        if error_count > 0:
            call.reason += f" {error_count} checkpoint(s) had errors."

    call.status = "completed" if call.status != "needs_manual_review" else call.status

    analysis_time = time.time() - t2
    log.info(f"COMPLETE → score={call.score}, compliant={call.compliant}, analysis={analysis_time:.1f}s")

    db.commit()

    return {
        "status": "done",
        "supplier": detected,
        "script": script.script_name,
        "score": call.score,
        "compliant": call.compliant,
        "filename": new_name,
    }


async def main():
    db = SessionLocal()

    # Get all calls with transcripts
    calls = db.query(Call).filter(
        Call.transcript.isnot(None),
        Call.status.in_(["completed", "needs_manual_review", "failed"]),
    ).all()

    log.info(f"{'='*60}")
    log.info(f"REPROCESS BATCH — {len(calls)} calls to process")
    log.info(f"{'='*60}")

    results = []
    for i, call in enumerate(calls):
        log.info(f"\n[{i+1}/{len(calls)}] Processing...")
        try:
            result = await reprocess_call(call, db)
            results.append(result)
        except Exception as e:
            log.error(f"REPROCESS ERROR call_id={call.id}: {e}")
            results.append({"status": "error", "error": str(e)})

    # Summary
    log.info(f"\n{'='*60}")
    log.info(f"REPROCESS SUMMARY")
    log.info(f"{'='*60}")
    done = [r for r in results if r["status"] == "done"]
    skipped = [r for r in results if r["status"] == "skipped"]
    no_script = [r for r in results if r["status"] == "no_script"]
    errors = [r for r in results if r["status"] == "error"]

    log.info(f"  Done:       {len(done)}")
    log.info(f"  Skipped:    {len(skipped)}")
    log.info(f"  No script:  {len(no_script)}")
    log.info(f"  Errors:     {len(errors)}")

    if done:
        log.info(f"\nResults:")
        for r in done:
            log.info(f"  {r['filename'][:60]:60s} | {r['supplier']:15s} | {r['script']:35s} | score={r['score']:6s} | compliant={r['compliant']}")

    if no_script:
        log.info(f"\nNo script matched:")
        for r in no_script:
            log.info(f"  supplier=\"{r['supplier']}\"")

    db.close()


if __name__ == "__main__":
    asyncio.run(main())
