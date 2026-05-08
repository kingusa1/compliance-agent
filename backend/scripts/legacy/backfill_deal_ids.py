"""One-shot backfill: assign deal_id to legacy calls by grouping on customer_name.

Idempotent. Safe to run multiple times. Finds existing CustomerDeal rows by
customer_name before creating new ones.
"""
from __future__ import annotations

from collections import defaultdict

from app.database import SessionLocal
from app.models import Call, CustomerDeal


def run() -> None:
    db = SessionLocal()
    try:
        orphan_calls = db.query(Call).filter(Call.deal_id.is_(None)).all()
        if not orphan_calls:
            print("no orphan calls; nothing to do")
            return

        groups: dict[str, list[Call]] = defaultdict(list)
        for call in orphan_calls:
            key = (call.customer_name or "UNKNOWN").strip() or "UNKNOWN"
            groups[key].append(call)

        created = 0
        linked = 0
        for customer_name, calls in groups.items():
            deal = (
                db.query(CustomerDeal)
                .filter(CustomerDeal.customer_name == customer_name)
                .first()
            )
            if deal is None:
                deal = CustomerDeal(customer_name=customer_name, status="in_progress")
                db.add(deal)
                db.flush()  # get generated id
                created += 1
            for c in calls:
                c.deal_id = deal.id
                if not c.call_type:
                    c.call_type = "full"
                linked += 1

        db.commit()
        print(f"created {created} deals; linked {linked} calls")
    finally:
        db.close()


if __name__ == "__main__":
    run()
