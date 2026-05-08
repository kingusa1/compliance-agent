"""Remove duplicate calls, keeping only the best one per base filename."""
import os
import sqlite3
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), "compliance.db")


def cleanup():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    rows = db.execute(
        "SELECT id, filename, status, created_at FROM calls ORDER BY created_at DESC"
    ).fetchall()

    # Group by base filename (strip supplier__script__ prefix)
    groups = defaultdict(list)
    for r in rows:
        name = r["filename"]
        if "__" in name:
            name = name.split("__")[-1]
        groups[name].append(dict(r))

    deleted = 0
    kept = 0
    for base, calls in groups.items():
        if len(calls) <= 1:
            kept += 1
            continue

        # Keep the best: completed > needs_manual_review > failed
        priority = {"completed": 0, "needs_manual_review": 1, "processing": 2, "failed": 3}
        calls.sort(key=lambda c: (priority.get(c["status"], 9),))
        keep = calls[0]
        remove = calls[1:]

        print(f"\n{base}:")
        print(f"  KEEP: {keep['id'][:8]} status={keep['status']} ({keep['filename'][:60]})")
        for r in remove:
            print(f"  DEL:  {r['id'][:8]} status={r['status']}")
            db.execute("DELETE FROM call_checkpoints WHERE call_id = ?", (r["id"],))
            db.execute("DELETE FROM calls WHERE id = ?", (r["id"],))
            deleted += 1
        kept += 1

    db.commit()
    print(f"\nDone: kept {kept}, deleted {deleted}")
    db.close()


if __name__ == "__main__":
    cleanup()
