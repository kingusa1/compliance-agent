"""Backfill embeddings for existing AgentLearning rows (Phase J Task 29).

Run once after deploying pgvector to populate embeddings on any rows that
were inserted before the feature flipped on. New rows auto-embed via
abstract_and_store_review().

Usage:
    cd backend && source venv/bin/activate && python backfill_learning_embeddings.py

Safe to re-run — only touches rows where embedding IS NULL.
"""
from app.agent.feedback import embed_text
from app.database import SessionLocal
from app.models import AgentLearning


def main() -> None:
    db = SessionLocal()
    try:
        pending = db.query(AgentLearning).filter(AgentLearning.embedding.is_(None)).all()
        total = len(pending)
        print(f"Backfilling {total} learnings...")
        done = 0
        skipped = 0
        for l in pending:
            emb = embed_text(l.pattern)
            if emb is None:
                skipped += 1
                continue
            l.embedding = emb
            done += 1
            if done % 25 == 0:
                db.commit()
                print(f"  {done}/{total} committed")
        db.commit()
        print(f"Done. embedded={done} skipped={skipped} (API failures leave row with NULL embedding).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
