from datetime import datetime

from app.models import AgentLearning


def test_agent_learning_creatable(test_db):
    learning = AgentLearning(
        supplier="E.ON Next",
        checkpoint_name="Agent confirms DOB",
        pattern="agent asked DOB in yes/no form without waiting for explicit customer confirmation",
        agent_verdict="pass",
        human_verdict="fail",
        lesson="customer_yes checkpoints require a clear verbal yes, not trailing silence",
    )
    test_db.add(learning)
    test_db.commit()

    fetched = test_db.query(AgentLearning).first()
    assert fetched is not None
    assert fetched.supplier == "E.ON Next"
    assert fetched.pattern.startswith("agent asked DOB")
    assert fetched.created_at is not None


def test_agent_learning_query_by_supplier_and_checkpoint(test_db):
    for i in range(3):
        test_db.add(AgentLearning(
            supplier="British Gas",
            checkpoint_name="Pricing disclosure",
            pattern=f"pattern {i}",
            agent_verdict="pass",
            human_verdict="fail",
            lesson=f"lesson {i}",
        ))
    test_db.add(AgentLearning(
        supplier="E.ON Next",
        checkpoint_name="Pricing disclosure",
        pattern="wrong supplier",
        agent_verdict="pass",
        human_verdict="pass",
        lesson="",
    ))
    test_db.commit()

    results = (
        test_db.query(AgentLearning)
        .filter_by(supplier="British Gas", checkpoint_name="Pricing disclosure")
        .all()
    )
    assert len(results) == 3


def test_agent_learning_has_no_pii_columns():
    # Verify schema contains only anonymized fields — no call_id, no excerpt, no transcript
    cols = {c.name for c in AgentLearning.__table__.columns}
    assert "call_id" not in cols
    assert "transcript_excerpt" not in cols
    assert "customer_name" not in cols
    assert "agent_name" not in cols
    # Required anonymized fields
    assert {"supplier", "checkpoint_name", "pattern", "agent_verdict",
            "human_verdict", "lesson", "created_at"}.issubset(cols)
